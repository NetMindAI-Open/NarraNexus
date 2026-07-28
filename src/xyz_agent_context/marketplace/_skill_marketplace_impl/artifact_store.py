"""
@file_name: artifact_store.py
@author: NetMind.AI
@date: 2026-07-21
@description: Object storage abstraction for marketplace skill artifacts.

boto3 appears ONLY in this file (spec §4): swapping S3 for R2/OSS/GCS later
touches nothing else. Selection:
- SKILL_S3_BUCKET env set  -> S3ArtifactStore (cloud deployments)
- otherwise                -> LocalArtifactStore under
  <base_working_path>/../marketplace_store (dev / tests / single-host)
"""

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from loguru import logger


class ArtifactStore(ABC):
    """Minimal artifact interface: content-addressed puts/gets by key."""

    @abstractmethod
    def put_file(self, key: str, src: Path) -> None: ...

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_to_path(self, key: str, dest: Path) -> Path: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalArtifactStore(ArtifactStore):
    """Filesystem-backed store (dev, tests, single-host cloud fallback)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"Artifact key escapes the store root: {key!r}")
        return path

    def put_file(self, key: str, src: Path) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def put_bytes(self, key: str, data: bytes) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def get_to_path(self, key: str, dest: Path) -> Path:
        src = self._path(key)
        if not src.exists():
            raise FileNotFoundError(f"Artifact not found: {key}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3ArtifactStore(ArtifactStore):
    """S3-backed store. The boto3 client is created lazily so importing this
    module never requires AWS credentials."""

    def __init__(self, bucket: str, prefix: str = "", region: Optional[str] = None):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region
        self._client = None

    def _s3(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put_file(self, key: str, src: Path) -> None:
        self._s3().upload_file(str(src), self.bucket, self._key(key))

    def put_bytes(self, key: str, data: bytes) -> None:
        self._s3().put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def get_to_path(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._s3().download_file(self.bucket, self._key(key), str(dest))
        return dest

    def exists(self, key: str) -> bool:
        """True iff the object is present. A genuine 404/NoSuchKey is False;
        any OTHER error (throttling, network, auth) is re-raised — swallowing
        it would return False and make the seed idempotency gate re-upload on
        every transient S3 hiccup."""
        from botocore.exceptions import ClientError

        try:
            self._s3().head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("404", "NoSuchKey", "NotFound") or status == 404:
                return False
            raise


def _env_segment() -> str:
    """Optional front segment of the S3 layout (e.g. "dev" / "prod") so ONE
    bucket cleanly serves multiple environments. Set MARKETPLACE_S3_ENV per
    deployment; the object layout becomes <env>/skills/... and <env>/teams/...
    Empty = flat layout (no env segment)."""
    return os.environ.get("MARKETPLACE_S3_ENV", "").strip("/")


def _compose_prefix(explicit_env: str, object_dir: str, flat_default: str) -> str:
    """Resolve the S3 key prefix. An explicit *_S3_PREFIX env always wins;
    otherwise compose <MARKETPLACE_S3_ENV>/<object_dir> (dev/skills,
    prod/teams, ...), falling back to the flat default when no env segment."""
    explicit = os.environ.get(explicit_env)
    if explicit is not None:
        return explicit.strip("/")
    seg = _env_segment()
    return f"{seg}/{object_dir}" if seg else flat_default


def get_artifact_store() -> ArtifactStore:
    bucket = os.environ.get("SKILL_S3_BUCKET")
    if bucket:
        return S3ArtifactStore(
            bucket=bucket,
            prefix=_compose_prefix("SKILL_S3_PREFIX", "skills", "narranexus-skills"),
            region=os.environ.get("SKILL_S3_REGION"),
        )
    from xyz_agent_context.settings import settings

    root = Path(settings.base_working_path).parent / "marketplace_store"
    logger.debug(f"ArtifactStore: SKILL_S3_BUCKET unset, using local store at {root}")
    return LocalArtifactStore(root)


def get_template_store() -> ArtifactStore:
    """Artifact store for Team Marketplace `.nxbundle` blobs — physically
    SEPARATE from skills (different S3 prefix / local subfolder).

    S3 layout (one bucket, dev/prod × skills/teams):
    - MARKETPLACE_S3_ENV=dev  -> keys under  dev/teams/...
    - MARKETPLACE_S3_ENV=prod -> keys under  prod/teams/...
    - unset                   -> flat "narranexus-teams/..."
    TEMPLATE_S3_PREFIX overrides the composed prefix; TEMPLATE_S3_BUCKET
    falls back to SKILL_S3_BUCKET (same bucket, own object dir).
    """
    bucket = os.environ.get("TEMPLATE_S3_BUCKET") or os.environ.get("SKILL_S3_BUCKET")
    if bucket:
        return S3ArtifactStore(
            bucket=bucket,
            prefix=_compose_prefix("TEMPLATE_S3_PREFIX", "teams", "narranexus-teams"),
            region=os.environ.get("TEMPLATE_S3_REGION") or os.environ.get("SKILL_S3_REGION"),
        )
    from xyz_agent_context.settings import settings

    root = Path(settings.base_working_path).parent / "marketplace_store" / "teams"
    logger.debug(f"TemplateStore: no S3 bucket set, using local store at {root}")
    return LocalArtifactStore(root)
