"""
@file_name: publish_skill.py
@author: NetMind.AI
@date: 2026-07-21
@description: CLI to publish a skill package to the Skill Marketplace.

Usage:
    # Publish one zip (or a skill directory, zipped on the fly) to a registry:
    uv run python scripts/publish_skill.py path/to/skill.zip \
        --registry https://agent.narra.nexus --token $MARKETPLACE_PUBLISH_TOKEN

    # Local dev (backend on :8000, MARKETPLACE_PUBLISH_TOKEN exported):
    uv run python scripts/publish_skill.py my-skill/ --registry http://localhost:8000

Exit codes: 0 published, 1 rejected by the security gate (report printed),
2 other error.
"""

import argparse
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx


def _zip_directory(skill_dir: Path) -> Path:
    zip_path = Path(tempfile.mkdtemp()) / f"{skill_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file() and path.name != ".skill_meta.json":
                zf.write(path, f"{skill_dir.name}/{path.relative_to(skill_dir).as_posix()}")
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a skill to the marketplace")
    parser.add_argument("package", help="Skill .zip file or skill directory")
    parser.add_argument("--registry", default=os.environ.get(
        "NARRANEXUS_MARKETPLACE_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("MARKETPLACE_PUBLISH_TOKEN"))
    parser.add_argument("--publisher", default="narranexus-team")
    args = parser.parse_args()

    if not args.token:
        # Local-mode registries accept tokenless publishes (loopback trust);
        # cloud registries will 403 without a valid token.
        print("note: no publish token set — only a local-mode registry will accept this")

    package = Path(args.package)
    if not package.exists():
        print(f"error: {package} does not exist", file=sys.stderr)
        return 2
    zip_path = package if package.is_file() else _zip_directory(package)

    url = f"{args.registry.rstrip('/')}/api/marketplace/skills/publish"
    with open(zip_path, "rb") as f:
        response = httpx.post(
            url,
            files={"file": (zip_path.name, f, "application/zip")},
            data={"publisher": args.publisher},
            headers={"X-Publish-Token": args.token} if args.token else {},
            timeout=120.0,
        )

    if response.status_code == 200:
        body = response.json()
        print(f"published: {body['skill_id']}@{body['version']} (scan: {body['scan_status']})")
        return 0
    if response.status_code == 422:
        detail = response.json().get("detail", {})
        print("REJECTED by the security gate:", file=sys.stderr)
        for issue in detail.get("scan_report", []):
            print(
                f"  [{issue['severity'].upper()}] {issue['rule']} "
                f"{issue['file']}:{issue['line']} — {issue['detail']}",
                file=sys.stderr,
            )
        return 1
    print(f"error {response.status_code}: {response.text}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
