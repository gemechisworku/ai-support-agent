"""Export the CallOutcome JSON schema for non-Python consumers and reviewers.

    uv run python -m supportagent.contracts.export           # write docs/contracts/call_outcome.schema.json
    uv run python -m supportagent.contracts.export --check   # exit 1 if the checked-in file is stale

A unit test fails when the checked-in schema is stale, so CI catches a contract change that was not
re-exported.
"""

import argparse
import json
import sys
from pathlib import Path

from supportagent.contracts.outcome import CallOutcome

SCHEMA_PATH = Path("docs/contracts/call_outcome.schema.json")


def render() -> str:
    return json.dumps(CallOutcome.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=SCHEMA_PATH)
    ap.add_argument("--check", action="store_true", help="only verify the file is current")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        current = args.path.exists() and args.path.read_text() == text
        print(f"{args.path} is {'up to date' if current else 'stale'}")
        return 0 if current else 1
    args.path.parent.mkdir(parents=True, exist_ok=True)
    args.path.write_text(text)
    print(f"wrote {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
