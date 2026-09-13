"""One cause must not produce two different answers.

With no CMU_CLI_ED_TOKEN, `ed courses` reported ED_AUTH_REQUIRED with the generic
"Check provider authorization and availability", while `ed threads` and `ed search`
carried a partial listing whose reason said "Set CMU_CLI_ED_TOKEN to an Ed API
token". The actionable sentence existed; half the commands discarded it.
"""

import pytest

from cmu_cli.ed_client import EdAuthError, EdError


def test_the_authored_messages_are_constants_not_interpolated():
    """The reason it is safe to show: no provider text can reach these."""
    for literal in (EdAuthError.NO_TOKEN, EdAuthError.DENIED, EdAuthError.REJECTED):
        assert isinstance(literal, str) and literal
        assert "{" not in literal and "%" not in literal


def test_a_missing_token_says_which_variable_to_set():
    assert "CMU_CLI_ED_TOKEN" in EdAuthError.NO_TOKEN


def test_an_auth_error_is_still_an_ed_error():
    """Existing `except EdError` handlers keep catching it."""
    assert issubclass(EdAuthError, EdError)
    with pytest.raises(EdError):
        raise EdAuthError(EdAuthError.NO_TOKEN)


def test_the_message_survives_being_raised():
    with pytest.raises(EdAuthError) as caught:
        raise EdAuthError(EdAuthError.NO_TOKEN)
    assert str(caught.value) == EdAuthError.NO_TOKEN
