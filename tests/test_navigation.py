"""The navigation is data, and these keep it honest.

The failure this is really guarding against is a screen added to a router and
never given a place in the nav: it works, nothing links to it, and nobody
finds out.
"""

import re
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.web import nav

client = TestClient(app)

ROUTERS = [Path("app/main.py"), *sorted(Path("app/web/routers").glob("*.py"))]
IN_NAV = {item.page for group in nav.GROUPS for item in group.items}


def _current(body: str) -> list[str]:
    """The labels of whatever the page marks as current, in document order."""
    return re.findall(r'<a href="[^"]*" class="on">([^<]+)</a>', body)


def screens_the_routers_render() -> set[str]:
    keys = set()
    for source in ROUTERS:
        keys |= set(re.findall(r'page="([a-z0-9-]+)"', source.read_text()))
    return keys


def test_every_screen_has_a_place_in_the_navigation():
    missing = screens_the_routers_render() - IN_NAV
    assert not missing, f"screens nothing links to: {sorted(missing)}"


def test_the_navigation_does_not_point_at_screens_that_are_gone():
    stale = IN_NAV - screens_the_routers_render()
    assert not stale, f"nav entries with no screen behind them: {sorted(stale)}"


def test_no_screen_sits_in_two_groups():
    counts = Counter(item.page for group in nav.GROUPS for item in group.items)
    assert [p for p, n in counts.items() if n > 1] == []


@pytest.mark.parametrize("item", [i for g in nav.GROUPS for i in g.items],
                         ids=lambda i: i.href)
def test_every_link_in_the_navigation_leads_somewhere(item):
    assert client.get(item.href).status_code == 200


@pytest.mark.parametrize("item", [i for g in nav.GROUPS for i in g.items],
                         ids=lambda i: i.href)
def test_a_screen_lights_up_its_own_group_and_itself(item):
    current = _current(client.get(item.href).text)
    assert len(current) == 2, "one group tab and one screen should be current"
    group, screen = current
    assert group == nav.active(item.page).label.replace("&", "&amp;")
    assert screen == item.label


def test_a_sub_page_stays_under_the_screen_it_belongs_to():
    """The extra-wage adjustment report is a view of Extra work, not a screen
    of its own, and the nav should keep saying so."""
    current = _current(client.get("/extra-work/adjustments").text)
    assert current == ["Labour", "Extra work"]


def test_an_unknown_screen_falls_back_rather_than_breaking():
    assert nav.active(None) is nav.GROUPS[0]
    assert nav.active("no-such-screen") is nav.GROUPS[0]
