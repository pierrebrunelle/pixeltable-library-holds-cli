"""Library hold queue, as Pixeltable tables, computed columns and @pxt.query functions.

`books` holds the catalog (ISBN primary key, copies per title); `holds` is the queue of patron
requests with computed shelf-slip codes and pickup deadlines. The `@pxt.query` functions are the
read side the CLI (`cli.py`) uses: the queue for a title, a patron's holds and the hold shelf of a
branch. Layout: UDFs live in udfs.py; this file holds the table models and queries.

    pxt schema update app.py libholds
    python cli.py seed
    python cli.py queue 9780143127741
"""

from __future__ import annotations

import pixeltable as pxt

from udfs import pickup_deadline, shelf_code, slow_label

TableModel = pxt.model_base()


class Books(TableModel, name='books'):
    """One title in the catalog."""

    isbn = pxt.Column(type=pxt.String, primary_key=True)
    title: pxt.String
    author: pxt.String
    copies: pxt.Int


class Holds(TableModel, name='holds'):
    """One patron hold request. status: waiting | ready | picked_up | expired | cancelled."""

    hold_id = pxt.Column(type=pxt.String, primary_key=True)
    isbn: pxt.String
    patron: pxt.String
    branch: pxt.String
    placed_at: pxt.Timestamp
    status: pxt.String
    ready_at: pxt.Timestamp | None
    label_secs: pxt.Float | None

    slip = shelf_code(hold_id, patron, branch)  # type: ignore[name-defined]  # noqa: F821
    pickup_by = pickup_deadline(ready_at)  # type: ignore[name-defined]  # noqa: F821
    label = slow_label(isbn, label_secs)  # type: ignore[name-defined]  # noqa: F821

    __indexes__ = [pxt.BtreeIndex(isbn), pxt.BtreeIndex(patron)]


@pxt.query
def title_queue(isbn: str):
    """Open holds for one title, oldest first (queue order)."""
    return (
        Holds.where((Holds.isbn == isbn) & ((Holds.status == 'waiting') | (Holds.status == 'ready')))
        .order_by(Holds.placed_at)
        .select(Holds.hold_id, Holds.patron, Holds.branch, Holds.status, Holds.placed_at, Holds.slip)
    )


@pxt.query
def patron_holds(patron: str):
    """Every hold a patron has, newest first."""
    return (
        Holds.where(Holds.patron == patron)
        .order_by(Holds.placed_at, asc=False)
        .select(Holds.hold_id, Holds.isbn, Holds.branch, Holds.status, Holds.pickup_by)
    )


@pxt.query
def hold_shelf(branch: str):
    """Holds waiting on a branch's hold shelf, by pickup deadline."""
    return (
        Holds.where((Holds.branch == branch) & (Holds.status == 'ready'))
        .order_by(Holds.pickup_by)
        .select(Holds.slip, Holds.patron, Holds.isbn, Holds.pickup_by)
    )
