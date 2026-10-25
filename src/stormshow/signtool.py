"""``storm-sign``: make a signed SB2 line by hand, for bench tests (test plan T2).

    storm-sign --secret-file /etc/storm/link.secret --seq 900001 STRIKE 80
    meshtastic --ch-index 1 --sendtext "<the printed line>"

Use sequence numbers well above the edge's (e.g. 900000+). The translator accepts each
number once, so the edge will later jump ahead automatically (STALE) if needed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import protocol as P


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="storm-sign", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--secret-file", required=True)
    ap.add_argument("--seq", type=int, required=True)
    ap.add_argument("verb", choices=sorted(P.VERBS))
    ap.add_argument("level", nargs="?", type=int)
    ap.add_argument("--verify", metavar="LINE", help="check a received request or reply line instead")
    args = ap.parse_args(argv)
    try:
        key = P.LinkKey.from_base64(Path(args.secret_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"storm-sign: {err}", file=sys.stderr)
        return 2
    if args.verify:
        parsed = P.parse_request(args.verify, key) or P.parse_reply(args.verify, key)
        print(parsed if parsed else "INVALID (bad syntax or signature)")
        return 0 if parsed else 1
    try:
        print(P.Request(args.seq, args.verb, args.level).encode(key))
    except ValueError as err:
        print(f"storm-sign: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
