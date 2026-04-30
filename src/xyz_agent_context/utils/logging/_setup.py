"""
@file_name: _setup.py
@author: Bin Liang
@date: 2026-04-28
@description: setup_logging — single entry point every process calls once.

Loads env-driven config, removes any pre-existing handlers, registers
the AUDIT custom level (no=25, between INFO and WARNING), installs the
stdlib intercept bridge, and adds two sinks: stderr + rotating file.

After the first call for a given service_name this becomes a no-op —
the same process is not expected to reconfigure logging mid-run, and
this invariant is what keeps the file-descriptor count O(1) per
process. (Re-running setup would otherwise stack handlers and pipes,
which is exactly the leak the previous LoggingService design hit on
EC2.)

Env vars (override function args):
  NEXUS_LOG_FORMAT=text|json    default: text
  NEXUS_LOG_LEVEL=TRACE|DEBUG|INFO|AUDIT|WARNING|ERROR|CRITICAL
  NEXUS_LOG_DIR=<path>          default: ~/.narranexus/logs/
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from loguru import logger

from ._intercept import install_intercept_handler


_DEFAULT_LOG_ROOT = Path.home() / ".narranexus" / "logs"

# Format with two trace fields baked in. Missing keys are filled with
# default placeholders by ``logger.configure(extra=...)`` below so
# missing fields show as dashes instead of raising KeyError.
_TEXT_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{extra[run_id]:>8} {extra[event_id]:>14} | "
    "{name}:{function}:{line} - {message}"
)

_RUN_ID_PLACEHOLDER = "--------"
_EVENT_ID_PLACEHOLDER = "--------------"

# Service name → resolved log directory. Presence in this dict means
# setup has already run for that service in this process.
_INITIALIZED: dict[str, Path] = {}


def _resolve_format(arg: str | None) -> Literal["text", "json"]:
    raw = (arg or os.environ.get("NEXUS_LOG_FORMAT") or "text").lower()
    return "json" if raw == "json" else "text"


def _resolve_level(arg: str | None) -> str:
    return (arg or os.environ.get("NEXUS_LOG_LEVEL") or "INFO").upper()


def _resolve_log_dir(arg: Path | None) -> Path:
    if arg is not None:
        return Path(arg)
    env_dir = os.environ.get("NEXUS_LOG_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return _DEFAULT_LOG_ROOT


def _ensure_audit_level() -> None:
    """Register AUDIT (no=25). Idempotent — loguru raises ValueError when
    a level is unknown, and re-registering the same level raises
    TypeError. We swallow both so the function is a true no-op on the
    second call within the same interpreter."""
    try:
        logger.level("AUDIT")
        return
    except ValueError:
        pass
    try:
        logger.level("AUDIT", no=25, color="<cyan>", icon="A")
    except TypeError:
        # Already added under a different code path (e.g. a competing
        # import order). Trust that it's our level — no harm.
        pass


def setup_logging(
    service_name: str,
    *,
    log_dir: Path | None = None,
    level: str | None = None,
    fmt: Literal["text", "json"] | None = None,
) -> Path:
    """Configure loguru sinks for the calling process.

    Parameters
    ----------
    service_name
        Used as the log subdirectory name and the file stem
        (``<service_name>_YYYYMMDD.log``).
    log_dir
        Override root log directory. Defaults to ``$NEXUS_LOG_DIR`` or
        ``~/.narranexus/logs``.
    level
        Minimum level for both stderr and file sinks. Default ``INFO``.
    fmt
        ``text`` (human-readable, default) or ``json`` (structured via
        loguru's serialize=True; verbose but jq-friendly).

    Returns
    -------
    Path
        The resolved log directory for this service. The file inside is
        named ``<service_name>_YYYYMMDD.log``.

    Idempotent: subsequent calls for the same service_name return the
    cached path without touching handlers.
    """
    if service_name in _INITIALIZED:
        return _INITIALIZED[service_name]

    resolved_level = _resolve_level(level)
    resolved_fmt = _resolve_format(fmt)
    resolved_dir = _resolve_log_dir(log_dir) / service_name
    resolved_dir.mkdir(parents=True, exist_ok=True)

    _ensure_audit_level()

    # Drop default DEBUG stderr handler that loguru installs at import.
    logger.remove()

    # Default values for trace fields so the format string never raises
    # on records emitted outside any bind_event() block.
    logger.configure(
        extra={
            "run_id": _RUN_ID_PLACEHOLDER,
            "event_id": _EVENT_ID_PLACEHOLDER,
        }
    )

    log_file = str(resolved_dir / f"{service_name}_{{time:YYYYMMDD}}.log")

    if resolved_fmt == "json":
        # serialize=True dumps the entire record (incl. extra) as JSON.
        # Field set is verbose (process/thread/file/...) but zero-config
        # and stable; an opinionated leaner JSON schema is a v2 task.
        logger.add(
            sys.stderr,
            level=resolved_level,
            serialize=True,
            backtrace=True,
            diagnose=False,
        )
        logger.add(
            log_file,
            level=resolved_level,
            serialize=True,
            rotation="00:00",
            retention="30 days",
            compression="zip",
            encoding="utf-8",
            enqueue=True,
            backtrace=True,
            diagnose=False,
        )
    else:
        logger.add(
            sys.stderr,
            format=_TEXT_FORMAT,
            level=resolved_level,
            backtrace=True,
            diagnose=False,
        )
        logger.add(
            log_file,
            format=_TEXT_FORMAT,
            level=resolved_level,
            rotation="00:00",
            retention="30 days",
            compression="zip",
            encoding="utf-8",
            enqueue=True,
            backtrace=True,
            diagnose=False,
        )

    install_intercept_handler()

    _INITIALIZED[service_name] = resolved_dir
    logger.info(
        "logging.setup service={s} fmt={f} level={l} dir={d}",
        s=service_name,
        f=resolved_fmt,
        l=resolved_level,
        d=str(resolved_dir),
    )
    return resolved_dir


def _reset_for_tests() -> None:
    """Internal: drop the initialization cache. Tests use this to force
    a fresh setup. Not exported."""
    _INITIALIZED.clear()
