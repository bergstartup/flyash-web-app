"""The navigation, as data.

Fifteen screens in one flat row had stopped being navigable, and every router
was hand-writing its own ``<a class="on if page == ...">``. The structure lives
here instead: groups of pages, in the order the plant works through them.

A screen's ``page`` key is what the routers already pass to the template, so
adding a screen means adding one :class:`Item` and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    page: str
    label: str
    href: str


@dataclass(frozen=True)
class Group:
    label: str
    items: tuple[Item, ...]

    @property
    def href(self) -> str:
        """Where the group's own tab goes: its first screen."""
        return self.items[0].href

    def holds(self, page: str) -> bool:
        return any(item.page == page for item in self.items)


GROUPS: tuple[Group, ...] = (
    Group("Overview", (
        Item("dashboard", "Dashboard", "/"),
        Item("daily", "Daily round", "/daily"),
        Item("reconciliation", "Reconciliation", "/reconciliation"),
        Item("anomalies", "Error register", "/anomalies"),
        Item("opening", "Opening figures", "/opening"),
        Item("import", "Import a month", "/import"),
    )),
    Group("Labour", (
        Item("attendance", "Attendance", "/attendance"),
        Item("extra-work", "Extra work", "/extra-work"),
        Item("advances", "Advances", "/advances"),
        Item("salary", "Salary", "/salary"),
        Item("labour", "Staff", "/labour"),
        Item("extra-work-types", "Work types", "/extra-work-types"),
    )),
    Group("Sales & stock", (
        Item("sales", "Sales", "/sales"),
        Item("production", "Production", "/production"),
        Item("stock", "Stock ledger", "/stock"),
        Item("products", "Products", "/products"),
    )),
    Group("Materials", (
        Item("materials", "Deliveries", "/materials"),
        Item("consumption", "Consumption", "/consumption"),
        Item("raw-materials", "Materials & suppliers", "/raw-materials"),
    )),
    Group("Plant", (
        Item("power", "Power", "/power"),
        Item("ledger", "Digital ledger", "/ledger"),
    )),
)


def active(page: str | None) -> Group:
    """The group a screen belongs to, falling back to the first."""
    for group in GROUPS:
        if page and group.holds(page):
            return group
    return GROUPS[0]
