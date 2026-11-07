"""CLI: python -m supportagent.bank.seed --seed 42 --as-of 2026-11-09 [--reset]

`--as-of` defaults to BANK_AS_OF, else today (UTC). Without `--reset` it refuses to seed a non-empty DB.
"""

import argparse
import datetime as dt
import sys
import time

from sqlalchemy import text

from supportagent.bank.seed import generate
from supportagent.bank.seed.writer import checksum, write
from supportagent.common import clock
from supportagent.common.db import bank_engine, session_scope
from supportagent.common.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m supportagent.bank.seed", description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--as-of", type=dt.date.fromisoformat, default=None)
    ap.add_argument("--reset", action="store_true", help="empty all bank tables first")
    args = ap.parse_args(argv)
    as_of = args.as_of or get_settings().data.bank_as_of or clock.today()

    started = time.monotonic()
    with session_scope(bank_engine()) as session:
        if not args.reset and session.execute(text("SELECT count(*) FROM customers")).scalar_one():
            print("bank database already has data; pass --reset to replace it", file=sys.stderr)
            return 1
        counts = write(session, generate(args.seed, as_of), reset_first=args.reset)
        digest = checksum(session)
    total = ", ".join(f"{k}={v}" for k, v in counts.items())
    print(f"seeded bank (seed={args.seed}, as_of={as_of}) in {time.monotonic() - started:.1f}s: {total}")
    print(f"checksum {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
