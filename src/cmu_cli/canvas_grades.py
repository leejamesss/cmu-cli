"""Own Canvas grades; server values only, never transcript grades or estimates."""

import json

from .canvas_client import CanvasEndpointUnavailable, CanvasError
from .submissions import submission_state


def identifier(value):
    if type(value) is not int or value < 1:
        raise CanvasError("Canvas returned an invalid identifier")
    return value


def unique(rows, key="id"):
    if not isinstance(rows, list):
        raise CanvasError("Canvas returned an invalid collection")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise CanvasError("Canvas returned an invalid record")
        identity = identifier(row.get(key))
        if identity in result and result[identity] != row:
            raise CanvasError("Canvas returned conflicting duplicate records")
        result[identity] = row
    return list(result.values())


def normalize_grade(assignment, submission, course, details=False):
    submission = submission or {}
    row = {
        "source": "canvas",
        "course": course,
        "kind": "assignment",
        "id": str(assignment["id"]),
        "name": assignment.get("name"),
        "points_possible": assignment.get("points_possible"),
        "score": submission.get("score"),
        "grade": submission.get("grade"),
        "release_state": "posted" if submission.get("posted_at") else "unknown",
        "submission_state": submission.get("workflow_state") or "unknown",
        "submitted": submission_state(
            submission.get("workflow_state"), submission.get("submitted_at")
        ),
        "url": assignment.get("html_url"),
        "provenance": "canvas.self_submissions",
        "unavailable_reason": {},
    }
    for key in (
        "excused",
        "missing",
        "late",
        "attempt",
        "graded_at",
        "posted_at",
        "grade_matches_current_submission",
    ):
        row[key] = submission.get(key)
    for key in ("score", "grade"):
        if row[key] is None:
            row["unavailable_reason"][key] = "not_returned_or_null"
    for key in ("submission_comments", "rubric_assessment"):
        row[key + "_availability"] = (
            (
                "returned"
                if key in submission and submission[key] is not None
                else "unknown"
            )
            if details
            else "not_requested"
        )
        if details:
            row[key] = submission.get(key)
    return row


def own_assignments(client, course_id, user_id, details=False):
    assignments = unique(client.assignments(course_id))
    params: dict = {"per_page": 100}
    if details:
        params["include[]"] = ["submission_comments", "rubric_assessment"]
    submissions = unique(
        client.get(
            f"/api/v1/courses/{identifier(course_id)}/students/submissions", params
        ),
        "assignment_id",
    )
    by_id = {}
    assignment_ids = {a["id"] for a in assignments}
    for assignment in assignments:
        if assignment.get("course_id", course_id) != course_id:
            raise CanvasError("Canvas assignment course mismatch")
    for submission in submissions:
        if (
            submission.get("user_id") != user_id
            or submission.get("course_id", course_id) != course_id
            or submission["assignment_id"] not in assignment_ids
        ):
            raise CanvasError("Canvas submission scope mismatch")
        by_id[submission["assignment_id"]] = submission
    return assignments, by_id


def own_enrollments(client, user_id, course_id):
    rows = unique(
        client.get(
            f"/api/v1/users/{identifier(user_id)}/enrollments",
            {
                "per_page": 100,
                "type[]": ["StudentEnrollment"],
                "state[]": ["current_and_concluded"],
                "include[]": ["current_points"],
            },
        )
    )
    result = []
    for row in rows:
        if row.get("user_id") != user_id:
            raise CanvasError("Canvas enrollment user mismatch")
        if row.get("course_id") != course_id:
            continue
        if row.get("type") != "StudentEnrollment":
            raise CanvasError("Canvas enrollment type mismatch")
        grades = row.get("grades") or {}
        fields = (
            "current_grade",
            "final_grade",
            "current_score",
            "final_score",
            "current_points",
        )
        result.append(
            {
                "source": "canvas",
                "kind": "course_total",
                "id": str(row["id"]),
                "course_id": course_id,
                "course_section_id": row.get("course_section_id"),
                "enrollment_state": row.get("enrollment_state"),
                "grading_period": "whole_course",
                "official_sio_grade": False,
                **{key: grades.get(key) for key in fields},
                "unavailable_reason": {
                    key: "not_returned_or_null"
                    for key in fields
                    if grades.get(key) is None
                },
            }
        )
    return result


def grade_fetch(source, course, function):
    """Keep the common envelope while identifying known permission failures."""
    from . import cli

    reason = None

    def read():
        nonlocal reason
        try:
            return function()
        except CanvasEndpointUnavailable as exc:
            reason = {
                401: "authentication_required",
                403: "permission_denied",
                404: "not_found",
            }.get(exc.status_code)
            raise

    result = cli.fetch(source, course, read)
    if reason:
        cli._CONTEXT["sources"][-1]["unavailable_reason"] = reason
        cli._CONTEXT["warnings"][-1]["unavailable_reason"] = reason
    return result


def own_identity(client):
    profile = client.get("/api/v1/users/self/profile")
    if not isinstance(profile, dict):
        raise CanvasError("Canvas returned an invalid profile")
    return identifier(profile.get("id"))


def command_grades(args, config, client):
    from . import cli, style

    if getattr(args, "source", "canvas") == "gradescope":
        from .gradescope_commands import command_gradescope

        return command_gradescope(args, config)
    user_id = grade_fetch("canvas.self_profile", None, lambda: own_identity(client))
    result = {
        "assignments": [],
        "enrollments": [],
        "scope": "configured courses; own visible Canvas grades, not SIO transcript",
    }
    for course in cli.select_courses(config, args.course) if user_id else []:
        payload = grade_fetch(
            "canvas.self_submissions",
            course.code,
            lambda course=course: own_assignments(
                client, course.canvas_id, user_id, args.details
            ),
        )
        if payload:
            assignments, submissions = payload
            missing = [a["id"] for a in assignments if a["id"] not in submissions]
            if missing:
                cli._CONTEXT["sources"][-1]["status"] = "partial"
                cli._CONTEXT["warnings"].append(
                    {
                        "code": "SUBMISSIONS_NOT_RETURNED",
                        "source": "canvas.self_submissions",
                        "course": course.code,
                        "assignment_ids": missing,
                    }
                )
            result["assignments"].extend(
                normalize_grade(a, submissions.get(a["id"]), course.code, args.details)
                for a in assignments
            )
        totals = grade_fetch(
            "canvas.self_enrollments",
            course.code,
            lambda course=course: own_enrollments(client, user_id, course.canvas_id),
        )
        result["enrollments"].extend({**row, "course": course.code} for row in totals)
    if args.json:
        cli.print_json(result)
    else:

        def plain():
            for row in result["assignments"]:
                print(
                    f"◆ {row['course']} | {row['name']} | score={cli._text(row['score'])}/{cli._text(row['points_possible'])} | grade={cli._text(row['grade'])} | {row['submission_state']} | release={row['release_state']}"
                )

        style.render(
            "Canvas grades (not SIO)",
            [
                ("Course", "course"),
                ("Assignment", "name"),
                ("Score", "score"),
                ("Possible", "points_possible"),
                ("Grade", "grade"),
                ("State", "submission_state"),
                ("Release", "release_state"),
            ],
            result["assignments"],
            plain,
            empty="No assignment grades returned; inspect source status.",
        )
        if args.details:
            for row in result["assignments"]:
                print(
                    f"  Feedback for {row['course']} assignment {row['id']}: "
                    + json.dumps(
                        {
                            key: row.get(key)
                            for key in ("submission_comments", "rubric_assessment")
                        },
                        ensure_ascii=True,
                    )
                )
        for row in result["enrollments"]:
            print(
                f"◆ {row['course']} | Canvas current={cli._text(row['current_score'])} final={cli._text(row['final_score'])} | current grade={cli._text(row['current_grade'])} final grade={cli._text(row['final_grade'])} | enrollment {row['id']} (not SIO)"
            )
    return 3 if cli._CONTEXT["warnings"] else 0
