"""UDFs for the library holds CLI (pure Python, no table imports)."""

from __future__ import annotations

import datetime as dt
import hashlib
import time

import pixeltable as pxt

PICKUP_DAYS = 7
BRANCH_SHELVES = {'central': 'C', 'eastside': 'E', 'riverside': 'R', 'hillcrest': 'H'}


@pxt.udf
def pickup_deadline(ready_at: dt.datetime | None, days: int = PICKUP_DAYS) -> dt.datetime | None:
    """When a ready hold expires if nobody picks it up."""
    if ready_at is None:
        return None
    return ready_at + dt.timedelta(days=days)


@pxt.udf
def shelf_code(hold_id: str, patron: str, branch: str) -> str:
    """Hold-shelf slip code: branch letter + last-name initials + 4 hex digits (stable per hold)."""
    letter = BRANCH_SHELVES.get(branch.lower(), 'X')
    initials = ''.join(p[0] for p in patron.split()[-1:]).upper() or 'X'
    digest = hashlib.sha1(f'{hold_id}:{patron}'.encode()).hexdigest()[:4].upper()
    return f'{letter}-{initials}{digest}'


@pxt.udf
def wait_band(queue_len: int, copies: int) -> str:
    """Rough wait estimate from queue length and copies (3-week loans)."""
    if copies <= 0:
        return 'on order'
    weeks = (queue_len // max(copies, 1)) * 3
    if weeks == 0:
        return 'available now'
    if weeks <= 3:
        return '1-3 weeks'
    if weeks <= 9:
        return '4-9 weeks'
    return '10+ weeks'


@pxt.udf
def slow_label(isbn: str, secs: float = 0.0) -> str:
    """Simulated slow label render (e.g. a printer/notify call); used to exercise background jobs."""
    if secs > 0:
        time.sleep(secs)
    return f'HOLD {isbn}'
