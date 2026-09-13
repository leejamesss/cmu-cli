"""The CLI's own validation must say what was wrong; providers stay sanitized.

Sanitizing provider failures is deliberate -- a raw exception can carry a URL, a
response body or a path. It was applied to the CLI's own validation too, so a
mistyped course code, a missing --course and an unreachable Canvas all printed
CONFIG_OR_OPERATION_FAILED and exit 2, with no way to tell them apart and no
--verbose to fall back on.
"""

import pytest

from cmu_cli import cli
from cmu_cli.models import Config, Course, UsageError


def _config(*codes):
    return Config(
        canvas_base_url="https://canvas.example.invalid",
        storage_root=None,
        term="T",
        timezone="America/New_York",
        courses=[
            Course(code, i, code, code.lower()) for i, code in enumerate(codes, 1)
        ],
    )


def test_an_unknown_course_names_the_configured_ones():
    with pytest.raises(UsageError) as caught:
        _config("AAA-101", "BBB-202").find_course("NOPE")
    message = str(caught.value)
    assert "NOPE" in message and "AAA-101" in message and "BBB-202" in message


def test_an_ambiguous_course_names_the_candidates():
    with pytest.raises(UsageError) as caught:
        _config("11705-A", "11777-A").find_course("117")
    assert "11705-A" in str(caught.value) and "11777-A" in str(caught.value)


def test_a_usage_error_is_still_a_value_error():
    """Existing callers catch ValueError; that contract is unchanged."""
    assert issubclass(UsageError, ValueError)


def test_the_sanitizer_lets_usage_errors_through_unchanged():
    with pytest.raises(UsageError, match="mistyped"):  # noqa: SIM117
        with cli.sanitized_errors():
            raise UsageError("mistyped")


def test_the_sanitizer_still_hides_provider_detail():
    with pytest.raises(cli.OperationError) as caught:  # noqa: SIM117
        with cli.sanitized_errors():
            raise RuntimeError("https://canvas.example/secret?token=abc")
    assert "canvas.example" not in str(caught.value)
    assert str(caught.value) == "CONFIG_OR_OPERATION_FAILED"


def test_every_sanitized_code_carries_a_hint():
    """A code without an explanation is the problem this is fixing."""
    codes = {
        "CONFIG_NOT_FOUND",
        "CONFIG_ALREADY_EXISTS",
        "CONFIG_INVALID_JSON",
        "CONFIG_OR_OPERATION_FAILED",
    }
    assert codes <= set(cli.ERROR_HINTS)
    assert all(cli.ERROR_HINTS[code].strip() for code in codes)
