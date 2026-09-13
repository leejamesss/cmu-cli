"""Shared tri-state submission semantics for CLI and local exports."""


def submission_state(status, submitted_at=None):
    if submitted_at:
        return True
    value = str(status or "").strip().lower().replace("_", " ")
    if value in {"not submitted", "no submission", "unsubmitted", "missing"}:
        return False
    if value in {"submitted", "graded", "pending review", "late"}:
        return True
    return None
