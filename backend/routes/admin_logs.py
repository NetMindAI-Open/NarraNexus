"""
@file_name: admin_logs.py
@author: Bin Liang
@date: 2026-04-28
@description: Operator-facing log inspection endpoints.

Cloud deploys (EC2 / Web mode) had no way to look at the per-process
log files without ssh. These endpoints expose the contents of
``~/.narranexus/logs/<service>/`` over HTTP so the SystemPage in the
frontend can tail and search them.

Layout assumed by the handlers (matches utils.logging.setup_logging
and utils.service_logger before it):

    NEXUS_LOG_DIR/                       # default ~/.narranexus/logs
    └── <service>/
        ├── <service>_YYYYMMDD.log
        └── <service>_YYYYMMDD-1.log.zip   # compressed rotation

Endpoints:
- GET  /api/admin/logs/services                 list services + dates
- GET  /api/admin/logs/{service}/tail           last N lines, optional level filter
- GET  /api/admin/logs/{service}/download       full file (today by default)
- GET  /api/admin/logs/event/{event_id}         lines containing this event_id

All endpoints require auth in cloud mode (the auth_middleware in
backend/main.py + QUOTA_BYPASS_PREFIXES already exempt /api/admin from
the quota gate). Local mode is unauthenticated by design — single-user
trust on loopback.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger


router = APIRouter()


# --- Configuration ---------------------------------------------------------

def _log_root() -> Path:
    """Resolve the active log root the same way setup_logging does."""
    env = os.environ.get("NEXUS_LOG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".narranexus" / "logs"


# Service folder name pattern — alphanumeric / underscore / dash.
# Used to defend against path-traversal in {service} path params.
_SAFE_SERVICE_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_SAFE_DATE_RE = re.compile(r"^\d{8}$")
_SAFE_EVENT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def _resolve_service_dir(service: str) -> Path:
    if not _SAFE_SERVICE_RE.match(service):
        raise HTTPException(status_code=400, detail="invalid service name")
    base = _log_root() / service
    if not base.is_dir():
        raise HTTPException(status_code=404, detail=f"no logs for service {service}")
    return base


def _require_staff(request: Request) -> None:
    """Refuse non-staff callers in cloud mode. Local mode is open."""
    role = getattr(request.state, "role", None)
    if role is None:
        # Local mode — auth_middleware short-circuited before populating
        # request.state. Single-user trust on loopback.
        return
    if role != "staff":
        raise HTTPException(status_code=403, detail="staff role required")


# --- Helpers ---------------------------------------------------------------

def _list_files(service_dir: Path) -> list[dict]:
    """Return a sorted-by-name-desc list of log file metadata."""
    items: list[dict] = []
    for p in service_dir.iterdir():
        if not p.is_file():
            continue
        if not (p.suffix == ".log" or p.name.endswith(".log.zip")):
            continue
        stat = p.stat()
        items.append({
            "name": p.name,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "compressed": p.name.endswith(".zip"),
        })
    items.sort(key=lambda it: it["name"], reverse=True)
    return items


def _today_log_path(service_dir: Path, service: str) -> Optional[Path]:
    today = date.today().strftime("%Y%m%d")
    p = service_dir / f"{service}_{today}.log"
    return p if p.is_file() else None


def _resolve_log_path(
    service_dir: Path, service: str, date_str: Optional[str]
) -> Path:
    if date_str is None:
        p = _today_log_path(service_dir, service)
        if p is None:
            raise HTTPException(status_code=404, detail="no log file for today")
        return p
    if not _SAFE_DATE_RE.match(date_str):
        raise HTTPException(status_code=400, detail="date must be YYYYMMDD")
    raw = service_dir / f"{service}_{date_str}.log"
    if raw.is_file():
        return raw
    zipped = service_dir / f"{service}_{date_str}.log.zip"
    if zipped.is_file():
        return zipped
    raise HTTPException(status_code=404, detail="no log file for that date")


def _tail_lines(path: Path, n: int) -> list[str]:
    """Read the last *n* lines of a text file. Compressed files get a
    one-shot decompression first; not memory-efficient for huge zips but
    rotated files are bounded by daily volume."""
    if path.name.endswith(".log.zip"):
        import zipfile
        with zipfile.ZipFile(path) as zf:
            inner = zf.namelist()[0]
            with zf.open(inner) as fh:
                data = fh.read().decode("utf-8", errors="replace").splitlines()
        return data[-n:]
    # Plain file — seek-based tail to avoid loading the whole thing.
    chunk = 65_536
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        buf = bytearray()
        while size > 0 and buf.count(b"\n") <= n:
            read = min(chunk, size)
            size -= read
            fh.seek(size)
            buf[:0] = fh.read(read)
    text = buf.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-n:]


def _filter_by_level(lines: Iterable[str], level: Optional[str]) -> list[str]:
    if not level:
        return list(lines)
    target = level.upper()
    # Format from setup_logging:
    #   YYYY-MM-DD HH:mm:ss.SSS | LEVEL    | run_id event_id | logger - msg
    # The level token is left-padded to 8 chars between two pipes.
    needle = f"| {target:<8} |"
    return [ln for ln in lines if needle in ln]


# --- Endpoints -------------------------------------------------------------

@router.get("/services")
async def list_services(request: Request) -> JSONResponse:
    """List services that have produced log files."""
    _require_staff(request)
    root = _log_root()
    if not root.is_dir():
        return JSONResponse({"services": []})
    services = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        services.append({
            "name": child.name,
            "files": _list_files(child),
        })
    services.sort(key=lambda s: s["name"])
    return JSONResponse({"services": services})


@router.get("/{service}/tail")
async def tail_logs(
    request: Request,
    service: str,
    n: int = Query(500, ge=1, le=10_000),
    level: Optional[str] = Query(None),
    date_str: Optional[str] = Query(None, alias="date"),
) -> JSONResponse:
    """Return the last *n* lines of <service>'s log file. Optional level
    filter narrows by severity. Optional date selects a rotated file
    (YYYYMMDD)."""
    _require_staff(request)
    service_dir = _resolve_service_dir(service)
    path = _resolve_log_path(service_dir, service, date_str)
    try:
        lines = _tail_lines(path, n)
    except Exception as exc:  # pragma: no cover - I/O errors are surfaced
        logger.exception("admin_logs.tail failed for {p}: {e}", p=path, e=exc)
        raise HTTPException(status_code=500, detail="failed to read log")
    lines = _filter_by_level(lines, level)
    return JSONResponse({
        "service": service,
        "file": path.name,
        "count": len(lines),
        "lines": lines,
    })


@router.get("/{service}/download")
async def download_log(
    request: Request,
    service: str,
    date_str: Optional[str] = Query(None, alias="date"),
) -> FileResponse:
    """Stream the full log file (today's by default) to the caller."""
    _require_staff(request)
    service_dir = _resolve_service_dir(service)
    path = _resolve_log_path(service_dir, service, date_str)
    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/zip" if path.suffix == ".zip" else "text/plain",
    )


@router.get("/event/{event_id}")
async def event_lines(
    request: Request,
    event_id: str,
    n: int = Query(2000, ge=1, le=20_000),
    service: Optional[str] = Query(None),
) -> JSONResponse:
    """Return all log lines that contain ``event_id`` across services.
    With ``service`` query, restrict to one service's today log; without
    it, scans every service's today log."""
    _require_staff(request)
    if not _SAFE_EVENT_ID_RE.match(event_id):
        raise HTTPException(status_code=400, detail="invalid event_id")
    root = _log_root()
    if not root.is_dir():
        return JSONResponse({"event_id": event_id, "lines": []})

    targets: list[tuple[str, Path]] = []
    if service is not None:
        sd = _resolve_service_dir(service)
        p = _today_log_path(sd, service)
        if p is not None:
            targets.append((service, p))
    else:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            p = _today_log_path(child, child.name)
            if p is not None:
                targets.append((child.name, p))

    matches: list[dict] = []
    for svc_name, path in targets:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if event_id in line:
                        matches.append({"service": svc_name, "line": line.rstrip("\n")})
                        if len(matches) >= n:
                            break
        except OSError as exc:
            logger.warning("admin_logs.event read failed for {p}: {e}", p=path, e=exc)
        if len(matches) >= n:
            break

    return JSONResponse({"event_id": event_id, "count": len(matches), "lines": matches})
