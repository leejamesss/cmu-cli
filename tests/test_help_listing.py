"""Every subcommand must be described in `cmu-cli --help`.

argparse lists a subcommand in the help body only when ``add_parser`` was given a
help string. Several were built in loops without one, so the commands this tool
exists for -- assignments, courses, sync -- appeared in the usage line and nowhere
else, and materials/announcements printed as blank rows.
"""

import argparse

import pytest

from cmu_cli import cli


def _subparsers_action(root: argparse.ArgumentParser):
    return next(
        action
        for action in root._actions
        if isinstance(action, argparse._SubParsersAction)
    )


def subcommand_names() -> list[str]:
    return list(_subparsers_action(cli.parser()).choices)


def test_the_parser_offers_the_commands_the_readme_documents():
    names = subcommand_names()
    for documented in ("assignments", "courses", "sync", "quizzes", "materials"):
        assert documented in names


@pytest.mark.parametrize("name", subcommand_names())
def test_every_subcommand_has_a_help_string(name):
    choices = _subparsers_action(cli.parser())._choices_actions
    entry = next((c for c in choices if c.dest == name), None)
    assert entry is not None, f"{name} is missing from the help body entirely"
    assert entry.help, f"{name} is listed with a blank description"


@pytest.mark.parametrize("name", subcommand_names())
def test_every_subcommand_is_printed_in_the_help_text(name):
    text = cli.parser().format_help()
    body = text.split("options:")[0]
    assert f"    {name} " in body or f"    {name}\n" in body
