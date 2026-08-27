"""Freeze one exact GitHub Release entity and its existing asset baseline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from tools.verify_release_identity import IdentityError, freeze_remote_release
except ModuleNotFoundError:  # Direct execution places tools/ at sys.path[0].
    from verify_release_identity import IdentityError, freeze_remote_release  # type: ignore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--github-output", required=True, type=Path)
    args = parser.parse_args(argv)
    snapshot = freeze_remote_release(
        repository=args.repository,
        tag=args.tag,
        sha=args.sha,
        token=os.environ.get("GH_TOKEN", ""),
    )
    with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"release_id={snapshot.release_id}\n")
        output.write(f"release_node_id={snapshot.node_id}\n")
        output.write(f"release_assets_sha256={snapshot.assets_sha256}\n")
    print("release entity and asset baseline frozen")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IdentityError, OSError, ValueError) as exc:
        print(f"release identity freeze failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
