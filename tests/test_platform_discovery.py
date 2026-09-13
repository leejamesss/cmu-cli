"""A Canvas LTI tab is not a provider URL.

Canvas exposes Piazza and Gradescope as course tabs whose html_url lives on the
Canvas origin. Those launch points carry no Piazza class network ID and no
Gradescope course ID, so ``posts`` cannot read a feed from one. Reporting a tab as
the provider URL made ``status`` say Piazza was configured for a course where
``posts`` then returned nothing -- two commands disagreeing about the same course.
"""

from unittest.mock import Mock

from cmu_cli import cli
from cmu_cli.models import Course

CANVAS = "https://canvas.example.invalid"
PIAZZA_TAB = {"label": "Piazza", "html_url": "/courses/5/external_tools/11046"}
GRADESCOPE_TAB = {"label": "Gradescope", "html_url": "/courses/5/external_tools/22"}


def _client(tabs):
    client = Mock()
    client.base_url = CANVAS
    client.tabs.return_value = tabs
    return client


def _course(**kwargs):
    return Course("A", 5, "A", "a", **kwargs)


def test_a_canvas_tab_is_not_reported_as_the_piazza_url():
    result = cli.discovered_platform_urls(_client([PIAZZA_TAB]), _course())
    assert result["piazza"] is None
    assert result["piazza_canvas_tab"].startswith(CANVAS)


def test_a_configured_url_is_kept_verbatim():
    course = _course(piazza_url="https://piazza.com/class/abc123")
    result = cli.discovered_platform_urls(_client([PIAZZA_TAB]), course)
    assert result["piazza"] == "https://piazza.com/class/abc123"


def test_gradescope_tab_is_separated_the_same_way():
    result = cli.discovered_platform_urls(_client([GRADESCOPE_TAB]), _course())
    assert result["gradescope"] is None
    assert result["gradescope_canvas_tab"].startswith(CANVAS)


def test_tabs_without_a_url_are_ignored():
    result = cli.discovered_platform_urls(
        _client([{"label": "Piazza", "html_url": None}]), _course()
    )
    assert result["piazza"] is None and result["piazza_canvas_tab"] is None


def test_status_does_not_claim_configured_for_a_launch_tab_alone():
    course = {"piazza_configured": False, "piazza_in_canvas": True}
    assert cli.platform_state(course, "piazza") == "in Canvas, URL not configured"


def test_status_reports_a_configured_url_as_configured():
    course = {"piazza_configured": True, "piazza_in_canvas": True}
    assert cli.platform_state(course, "piazza") == "configured"


def test_status_reports_nothing_at_all_as_not_configured():
    course = {"piazza_configured": False, "piazza_in_canvas": False}
    assert cli.platform_state(course, "piazza") == "not configured"
