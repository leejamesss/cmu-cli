from types import SimpleNamespace
from typing import Any, cast

from cmu_cli.gradescope_client import GradescopeClient
from cmu_cli.models import Course


def test_assignment_parser_uses_due_time_not_release_time():
    html = """
    <table id="assignments-student-table"><tbody><tr>
      <th scope="row"><button data-assignment-id="202" data-assignment-title="Assignment 1">Assignment 1</button></th>
      <td><div class="submissionStatus--text">No Submission</div></td>
      <td>
        <time class="submissionTimeChart--releaseDate" datetime="2026-08-27 14:56:00 -0400">Aug 27 at 2:56PM</time>
        <time class="submissionTimeChart--dueDate" datetime="2026-09-03 15:00:00 -0400">Sep 03 at 3:00PM</time>
      </td>
    </tr></tbody></table>
    """

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return SimpleNamespace(
                status_code=200,
                url="https://www.gradescope.com/courses/101",
                text=html,
            )

    client = GradescopeClient.__new__(GradescopeClient)
    client.session = cast(Any, FakeSession())
    course = Course(
        code="DEMO-101",
        canvas_id=1,
        name="Example Course",
        directory="DEMO-101",
        gradescope_url="https://www.gradescope.com/courses/101",
    )

    rows = GradescopeClient.parse_assignments(course, html)
    assert rows == [
        {
            "name": "Assignment 1",
            "status": "No Submission",
            "released_due": "Sep 03 at 3:00PM",
            "due_at": "2026-09-03 15:00:00 -0400",
            "url": "https://www.gradescope.com/courses/101/assignments/202",
        }
    ]
