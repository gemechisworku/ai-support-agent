"""Write the console API's OpenAPI document to web/console/openapi.json (T-CON-09, CON-R11).

    uv run python scripts/export_console_openapi.py           # write
    uv run python scripts/export_console_openapi.py --check   # exit 1 if the committed file is stale

The Next.js console generates its TypeScript types from this file (`make console-types`), so a change
to a console view model shows up as a diff here and as a type error in the web app.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from supportagent.console.api import create_app

OUT = Path(__file__).resolve().parents[1] / "web" / "console" / "openapi.json"


def render() -> str:
    spec = create_app(token="openapi-export").openapi()  # noqa: S106 — a placeholder, never used to serve
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if the committed file differs")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        if not args.out.exists() or args.out.read_text() != text:
            print(f"{args.out} is out of date: run `make console-types`", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
