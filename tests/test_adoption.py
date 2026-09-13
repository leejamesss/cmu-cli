import pytest

from cmu_cli import cli
from cmu_cli.demo import run_demo
from cmu_cli.models import Course
from cmu_cli.storage import assignments_markdown


@pytest.mark.parametrize(
    "workflow,submitted_at,label",
    [
        ("unsubmitted", None, "未提交"),
        ("Not Submitted", None, "未提交"),
        ("graded", None, "已提交"),
        ("pending_review", None, "已提交"),
        (None, None, "未知"),
        ("excused", None, "未知"),
        (None, "2030-01-01T00:00:00Z", "已提交"),
    ],
)
def test_markdown_matches_cli(workflow, submitted_at, label):
    submission = {"workflow_state": workflow, "submitted_at": submitted_at}
    text = assignments_markdown(
        Course("DEMO", 1, "Demo", "demo"), [{"submission": submission}]
    )
    assert f"- 状态：{label}" in text
    assert {True: "已提交", False: "未提交", None: "未知"}[
        cli.submission_state(workflow, submitted_at)
    ] == label


def test_demo_workflow_is_reproducible():
    first = run_demo()
    assert first == run_demo()
    assert [a["submitted"] for a in first["assignments"]] == [True, False, None]
    assert ".cmucw/download_manifest.json" in first["exports"]
    assert "Bring your questions" in first["announcement_index"]
    assert "未知" in first["assignment_index"]


def test_demo_payload_schema_rejects_invalid_submission():
    import copy

    from jsonschema import ValidationError

    from tests.test_final_integration import VALIDATOR

    payload = {
        "schema_version": "1.0",
        "command": "demo",
        "status": "ok",
        "complete": True,
        "retrieved_at": "2030-01-01T00:00:00Z",
        "sources": [],
        "warnings": [],
        "error": None,
        "data": run_demo(),
    }
    VALIDATOR.validate(payload)
    invalid = copy.deepcopy(payload)
    invalid["data"]["assignments"][0]["submitted"] = "unknown"
    with pytest.raises(ValidationError):
        VALIDATOR.validate(invalid)
