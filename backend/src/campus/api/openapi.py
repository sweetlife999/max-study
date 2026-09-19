"""Export or check the public contract without settings, secrets, or a database."""

import argparse
import json
from pathlib import Path

from campus.api.app import create_app


def export() -> str:
    return json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = export()
    if args.check:
        if not args.path.is_file() or args.path.read_text(encoding="utf-8") != expected:
            parser.exit(1, "OpenAPI artifact is missing or stale; regenerate it.\n")
    else:
        args.path.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
