"""
@file_name: publish_batch.py
@author: NetMind.AI
@date: 2026-07-22
@description: Batch-publish a directory of skill .zip packages to a marketplace
registry — a thin loop over the same POST /api/marketplace/skills/publish
endpoint that scripts/publish_skill.py drives for one skill.

Usage:
    uv run python scripts/publish_batch.py <dir-of-zips> \
        --registry https://<backend> --token $MARKETPLACE_PUBLISH_TOKEN

Each package is scanned server-side; a HIGH-risk package is REJECTED (422)
with its report and skipped, the rest continue. Exit code is 0 when every
package either published or was already present, 1 if any were rejected,
2 on a transport/other error.

NOTE (licensing): this is an OPS tool for publishing packages you have the
right to redistribute. Third-party skills without a clear permissive license
should NOT be rehosted in a public marketplace — for those, prefer an
index-and-install-from-source model. See the team's marketplace notes.
"""

import argparse
import os
import sys
from pathlib import Path

import httpx


def _publish_one(client: httpx.Client, url: str, zip_path: Path, publisher: str,
                 token: str | None) -> str:
    headers = {"X-Publish-Token": token} if token else {}
    with open(zip_path, "rb") as f:
        resp = client.post(
            url,
            files={"file": (zip_path.name, f, "application/zip")},
            data={"publisher": publisher},
            headers=headers,
            timeout=120.0,
        )
    if resp.status_code == 200:
        body = resp.json()
        return f"published {body['skill_id']}@{body['version']} (scan: {body['scan_status']})"
    if resp.status_code == 409:
        return "already installed / same version"
    if resp.status_code == 422:
        detail = resp.json().get("detail", {})
        rules = sorted({i["rule"] for i in detail.get("scan_report", []) if i.get("severity") == "high"})
        return f"REJECTED by scan ({', '.join(rules)})"
    return f"ERROR {resp.status_code}: {resp.text[:120]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-publish skill packages")
    parser.add_argument("directory", help="Directory containing *.zip skill packages")
    parser.add_argument("--registry", default=os.environ.get(
        "NARRANEXUS_MARKETPLACE_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("MARKETPLACE_PUBLISH_TOKEN"))
    parser.add_argument("--publisher", default="narranexus-team")
    args = parser.parse_args()

    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return 2

    zips = sorted(directory.glob("*.zip"))
    if not zips:
        print(f"error: no .zip packages in {directory}", file=sys.stderr)
        return 2
    if not args.token:
        print("note: no publish token — only a local-mode registry will accept these")

    url = f"{args.registry.rstrip('/')}/api/marketplace/skills/publish"
    published = rejected = errored = 0
    print(f"Publishing {len(zips)} package(s) to {args.registry} ...\n")
    with httpx.Client() as client:
        for z in zips:
            try:
                outcome = _publish_one(client, url, z, args.publisher, args.token)
            except Exception as e:  # noqa: BLE001
                outcome = f"ERROR (transport): {e}"
            print(f"  {z.name:36} -> {outcome}")
            if outcome.startswith(("published", "already")):
                published += 1
            elif outcome.startswith("REJECTED"):
                rejected += 1
            else:
                errored += 1

    print(f"\nDone: {published} ok, {rejected} rejected, {errored} error(s).")
    if errored:
        return 2
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
