"""
@file_name: mysql_dialect.py
@author: NarraNexus
@date: 2026-08-15
@description: One place to describe "run this against a real MySQL".

Most dialect-twin test files carry their own copy of a `mysql://` URL parser
and their own phrasing of the skip reason. The parser is a few lines of string
splitting that nobody will ever change — which is exactly why the copies went
unnoticed — but the skip GATE is worth having once: a twin that reads the env
var by a slightly different name, or forgets the skipif entirely, fails on every
machine that has no MySQL and looks like a broken test rather than a missing
service.

This is the canonical home. Migrating the remaining copies is a matter for
whoever next touches them; nothing here changes their behaviour, and a shared
helper with one user is still better than one more copy, because the next person
has somewhere obvious to put theirs.

(Counts deliberately absent: this docstring used to say "nine"/"the other eight",
which went stale as soon as a tenth twin was written on another branch. There is
no number anywhere now — `tests/test_mysql_gate_single_source.py` asserts by
CONTENT that nothing gates on this env var from outside `*_mysql.py`, which is
the fact a count was standing in for, and it needs no maintenance when the set
of twins changes.)
"""

from __future__ import annotations

import os

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"


def parse_mysql_url(url: str) -> dict:
    """`mysql://user:pass@host:port/db` → the kwargs MySQLBackend wants."""
    assert url.startswith("mysql://"), f"expected mysql://..., got {url!r}"
    body = url[len("mysql://") :]
    creds, _, host_db = body.partition("@")
    user, _, password = creds.partition(":")
    host_port, _, database = host_db.partition("/")
    host, _, port = host_port.partition(":")
    return {
        "host": host,
        "port": int(port) if port else 3306,
        "user": user,
        "password": password,
        "database": database,
    }


def mysql_url() -> str:
    """The configured URL. Only call this past the skip gate."""
    return os.environ[MYSQL_URL_ENV]


def skip_reason(what: str) -> str:
    """The message a skipped dialect twin should print.

    Names WHAT would have been checked, not just that MySQL is missing: a bare
    "not configured" teaches the reader to ignore the skip, and these twins
    exist precisely for the failures that only appear in the other dialect.
    """
    return (
        f"{MYSQL_URL_ENV} not set. Against a real MySQL dialect, these tests "
        f"check {what}.\nStart one and export the URL to run them:\n"
        f"    docker run --rm -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root \\\n"
        f"        -e MYSQL_DATABASE=nxtest --name nx-mysql-test mysql:8\n"
        f"    export {MYSQL_URL_ENV}=mysql://root:root@127.0.0.1:3306/nxtest"
    )


def mysql_configured() -> bool:
    return bool(os.environ.get(MYSQL_URL_ENV))
