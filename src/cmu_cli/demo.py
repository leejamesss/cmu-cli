"""Reproducible synthetic coursework sync; never contacts a provider."""

from pathlib import Path
from tempfile import TemporaryDirectory

from .models import Config, Course
from .storage import sync_course
from .submissions import submission_state


def run_demo():
    with TemporaryDirectory(prefix="cmu-cli-demo-") as tmp:
        course = Course("DEMO-101", 1, "Synthetic Example", "demo")
        config = Config("https://canvas.example.invalid", Path(tmp), (course,))
        assignments = [
            {"id": i, "name": name, "due_at": None, "submission": submission}
            for i, name, submission in [
                (1, "Read the syllabus", {"workflow_state": "graded"}),
                (2, "Practice exercise", {"workflow_state": "unsubmitted"}),
                (3, "Project proposal", {}),
            ]
        ]
        content = b"Synthetic only"
        files = [
            {
                "id": 1,
                "display_name": "hw1.txt",
                "size": len(content),
                "updated_at": "v1",
                "url": "https://canvas.example.invalid/synthetic.txt",
            }
        ]
        announcements = [
            {
                "title": "Welcome",
                "message": "<p>Bring your questions to office hours.</p>",
            }
        ]
        first = sync_course(
            config,
            course,
            assignments,
            files,
            announcements,
            [],
            True,
            lambda _: content,
        )

        def no_fetch(_):
            raise AssertionError("Unchanged file must not be downloaded")

        second = sync_course(
            config, course, assignments, files, announcements, [], True, no_fetch
        )
        root = Path(first["root"])
        download = first["downloads"][0]
        assert (root / download["relative_path"]).read_bytes() == content
        return {
            "synthetic": True,
            "network_used": False,
            "statuses": [download["status"], second["downloads"][0]["status"]],
            "file": download["relative_path"],
            "sha256": download["sha256"],
            "assignments": [
                {
                    "name": a["name"],
                    "submitted": submission_state(
                        a["submission"].get("workflow_state")
                    ),
                }
                for a in assignments
            ],
            "exports": sorted(
                str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
            ),
            "assignment_index": (root / "02_作业/Canvas作业索引.md").read_text(
                encoding="utf-8"
            ),
            "announcement_index": (root / "00_课程信息/Canvas通知索引.md").read_text(
                encoding="utf-8"
            ),
        }
