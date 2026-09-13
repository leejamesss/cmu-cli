"""An incomplete result must say what was missing, not only that something was.

A per-course source failure leaves no trace in the plain output: the rows that did
arrive print normally, and one trailing line said results were incomplete. On a real
eight-course account, five courses failed a quizzes fetch and the reader had no way
to tell which -- so an empty result read as "no quizzes" rather than "not read".
"""

from cmu_cli.cli import incomplete_report


def _unavailable(source, course):
    return {"code": "SOURCE_UNAVAILABLE", "source": source, "course": course}


def test_a_failed_course_is_named():
    report = "\n".join(incomplete_report([_unavailable("canvas.quizzes", "11777-A")]))
    assert "canvas.quizzes" in report and "11777-A" in report


def test_courses_failing_the_same_source_share_one_line():
    warnings = [
        _unavailable("canvas.quizzes", "A"),
        _unavailable("canvas.quizzes", "B"),
        _unavailable("canvas.quizzes", "C"),
    ]
    lines = [line for line in incomplete_report(warnings) if "canvas.quizzes" in line]
    assert len(lines) == 1
    assert "A, B, C" in lines[0]


def test_different_sources_are_reported_separately():
    warnings = [
        _unavailable("canvas.quizzes", "A"),
        _unavailable("canvas.files", "A"),
    ]
    report = incomplete_report(warnings)
    assert sum("canvas.quizzes" in line for line in report) == 1
    assert sum("canvas.files" in line for line in report) == 1


def test_a_duplicate_course_is_not_repeated():
    warnings = [_unavailable("canvas.files", "A"), _unavailable("canvas.files", "A")]
    line = next(line for line in incomplete_report(warnings) if "canvas.files" in line)
    assert line.count("A") == 1


def test_a_source_without_a_course_scope_still_reports():
    report = "\n".join(incomplete_report([_unavailable("piazza.posts", None)]))
    assert "piazza.posts unavailable" in report


def test_truncation_says_how_much_was_withheld():
    warning = {"code": "TRUNCATED", "total_before_limit": 40, "limit": 5}
    report = "\n".join(incomplete_report([warning]))
    assert "5" in report and "40" in report


def test_the_json_pointer_is_kept():
    report = "\n".join(incomplete_report([_unavailable("canvas.files", "A")]))
    assert "--json" in report
