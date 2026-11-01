"""holds: a small command-line tool for a library hold queue, built on Pixeltable.

    python cli.py seed                       # 8 titles, 40 holds across 4 branches
    python cli.py place 9780143127741 "Ada Park" central
    python cli.py queue 9780143127741        # queue order for one title
    python cli.py patron "Ada Park"
    python cli.py ready 9780143127741        # next waiting hold -> ready (pickup clock starts)
    python cli.py shelf central              # the branch hold shelf with slip codes
    python cli.py stats                      # waiting/ready per title + wait estimate
    python cli.py doctor                     # where every setting comes from (pxt config) + secret names
    python cli.py submit URL --json '{...}'  # POST to a Pixeltable service, honoring Retry-After

Set PXT_TARGET to run against another catalog (default: libholds), e.g. a hosted database URI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import subprocess
import sys
import time
import uuid

import pixeltable as pxt

TARGET = os.environ.get('PXT_TARGET', 'libholds')

BOOKS = [
    ('9780143127741', 'The Overstory', 'Richard Powers', 2),
    ('9780593135204', 'Project Hail Mary', 'Andy Weir', 3),
    ('9780525559474', 'The Midnight Library', 'Matt Haig', 2),
    ('9780735219090', 'Where the Crawdads Sing', 'Delia Owens', 1),
    ('9780062316097', 'Sapiens', 'Yuval Noah Harari', 2),
    ('9781250301697', 'The Silent Patient', 'Alex Michaelides', 1),
    ('9780593230572', 'Tomorrow, and Tomorrow, and Tomorrow', 'Gabrielle Zevin', 2),
    ('9780316769488', 'The Catcher in the Rye', 'J. D. Salinger', 0),
]
PATRONS = ['Ada Park', 'Bo Chen', 'Cleo Diaz', 'Dev Rao', 'Eli Moss', 'Fay Ito', 'Gus Lund', 'Hana Ode']
BRANCHES = ['central', 'eastside', 'riverside', 'hillcrest']


def is_hosted(target: str) -> bool:
    """True for a hosted database URI (scheme `pxt`), False for a local catalog directory."""
    return target.split(':', 1)[0] == 'pxt'


def path(name: str) -> str:
    return f'{TARGET}/{name}'


def books() -> pxt.Table:
    return pxt.get_table(path('books'))


def holds() -> pxt.Table:
    return pxt.get_table(path('holds'))


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def show(rows) -> None:
    for r in rows:
        print('  ' + ' | '.join(f'{v}' for v in r.values()))


def cmd_seed(args: argparse.Namespace) -> None:
    b = books()
    b.insert([{'isbn': i, 'title': t, 'author': a, 'copies': c} for i, t, a, c in BOOKS], on_error='abort')
    rnd = random.Random(args.seed)
    start = now() - dt.timedelta(days=30)
    rows = []
    for n in range(args.n):
        isbn = rnd.choice(BOOKS)[0]
        rows.append(
            {
                'hold_id': f'H{n:04d}',
                'isbn': isbn,
                'patron': rnd.choice(PATRONS),
                'branch': rnd.choice(BRANCHES),
                'placed_at': start + dt.timedelta(hours=rnd.randint(0, 24 * 29)),
                'status': 'waiting',
                'ready_at': None,
                'label_secs': None,
            }
        )
    status = holds().insert(rows, on_error='abort')
    print(f'seeded {len(BOOKS)} books, {status.num_rows} holds')


def cmd_place(args: argparse.Namespace) -> None:
    hid = f'H{uuid.uuid4().hex[:8].upper()}'
    holds().insert(
        [{'hold_id': hid, 'isbn': args.isbn, 'patron': args.patron, 'branch': args.branch,
          'placed_at': now(), 'status': 'waiting', 'ready_at': None, 'label_secs': None}]
    )
    print(f'placed {hid}')


def cmd_queue(args: argparse.Namespace) -> None:
    h = holds()
    q = (
        h.where((h.isbn == args.isbn) & ((h.status == 'waiting') | (h.status == 'ready')))
        .order_by(h.placed_at)
        .select(h.hold_id, h.patron, h.branch, h.status, h.slip)
    )
    rows = list(q.collect())
    print(f'{len(rows)} open holds for {args.isbn}')
    for pos, r in enumerate(rows, 1):
        print(f'  #{pos:<2} {r["hold_id"]} {r["patron"]:<10} {r["branch"]:<9} {r["status"]:<7} {r["slip"]}')


def cmd_patron(args: argparse.Namespace) -> None:
    h = holds()
    show(h.where(h.patron == args.patron).order_by(h.placed_at, asc=False)
         .select(h.hold_id, h.isbn, h.branch, h.status, h.pickup_by).collect())


def cmd_ready(args: argparse.Namespace) -> None:
    h = holds()
    nxt = list(h.where((h.isbn == args.isbn) & (h.status == 'waiting')).order_by(h.placed_at).limit(1)
               .select(h.hold_id).collect())
    if not nxt:
        print('no waiting holds')
        return
    hid = nxt[0]['hold_id']
    h.update({'status': 'ready', 'ready_at': now()}, where=h.hold_id == hid)
    r = list(h.where(h.hold_id == hid).select(h.slip, h.pickup_by).collect())[0]
    print(f'{hid} ready: slip {r["slip"]}, pick up by {r["pickup_by"]}')


def cmd_shelf(args: argparse.Namespace) -> None:
    h = holds()
    show(h.where((h.branch == args.branch) & (h.status == 'ready')).order_by(h.pickup_by)
         .select(h.slip, h.patron, h.isbn, h.pickup_by).collect())


def cmd_expire(args: argparse.Namespace) -> None:
    h = holds()
    status = h.update({'status': 'expired'}, where=(h.status == 'ready') & (h.pickup_by < now()))
    print(f'expired {status.num_rows} holds')


def cmd_stats(args: argparse.Namespace) -> None:
    from udfs import wait_band

    h, b = holds(), books()
    copies = {r['isbn']: (r['title'], r['copies']) for r in b.select(b.isbn, b.title, b.copies).collect()}
    counts = h.where(h.status == 'waiting').group_by(h.isbn).select(h.isbn, n=pxt.functions.count(h.hold_id))
    for r in sorted(counts.collect(), key=lambda r: -r['n']):
        title, c = copies.get(r['isbn'], ('?', 0))
        print(f'  {title[:34]:<34} waiting={r["n"]:<3} copies={c} wait={wait_band.py_fn(r["n"], c)}')


def cmd_doctor(args: argparse.Namespace) -> None:
    """Show where each Pixeltable setting comes from, and which secret names the target has."""
    out = subprocess.run(['pxt', 'config', '--json'], capture_output=True, text=True)
    try:
        cfg = json.loads(out.stdout)
    except json.JSONDecodeError:
        print(out.stdout or out.stderr)
        return
    entries = cfg if isinstance(cfg, list) else cfg.get('settings', cfg.get('entries', []))
    by_source: dict[str, int] = {}
    for e in entries:
        src = e.get('source', '?')
        src = 'file' if src not in ('env', 'unset') else src
        by_source[src] = by_source.get(src, 0) + 1
        if src != 'unset' and not args.quiet:
            print(f'  {e.get("section", "")}.{e.get("key", e.get("name", ""))} <- {e.get("source")}')
    print('settings by source:', by_source)
    if is_hosted(TARGET):
        sec = subprocess.run(['pxt', 'secret', 'list', TARGET], capture_output=True, text=True)
        print(sec.stdout.strip() or sec.stderr.strip())


def submit_with_retry(url: str, body: dict, headers: dict | None = None, attempts: int = 8) -> dict:
    """POST to a Pixeltable service route and honor 503 + Retry-After (JOB_REFUSED, DATABASE_UNAVAILABLE)."""
    import requests

    for i in range(attempts):
        r = requests.post(url, json=body, headers=headers or {}, timeout=60)
        if r.status_code == 503 and 'Retry-After' in r.headers:
            detail = r.json().get('detail', {})
            wait = float(r.headers['Retry-After'])
            print(f'  attempt {i + 1}: 503 {detail.get("error_code")} retry in {wait:g}s', file=sys.stderr)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f'gave up after {attempts} attempts')


def cmd_submit(args: argparse.Namespace) -> None:
    headers = {}
    if os.environ.get('PIXELTABLE_API_KEY') and args.auth:
        headers['Authorization'] = f'Bearer {os.environ["PIXELTABLE_API_KEY"]}'
    print(json.dumps(submit_with_retry(args.url, json.loads(args.json), headers), default=str))


def main() -> None:
    p = argparse.ArgumentParser(prog='holds', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('seed'); s.add_argument('--n', type=int, default=40); s.add_argument('--seed', type=int, default=7)
    s = sub.add_parser('place'); s.add_argument('isbn'); s.add_argument('patron'); s.add_argument('branch')
    s = sub.add_parser('queue'); s.add_argument('isbn')
    s = sub.add_parser('patron'); s.add_argument('patron')
    s = sub.add_parser('ready'); s.add_argument('isbn')
    s = sub.add_parser('shelf'); s.add_argument('branch')
    sub.add_parser('expire')
    sub.add_parser('stats')
    s = sub.add_parser('doctor'); s.add_argument('--quiet', action='store_true')
    s = sub.add_parser('submit'); s.add_argument('url'); s.add_argument('--json', default='{}'); s.add_argument('--auth', action='store_true')
    args = p.parse_args()
    globals()[f'cmd_{args.cmd}'](args)


if __name__ == '__main__':
    main()
