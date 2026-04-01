"""
Lightweight asyncpg-based migration runner.

Migration files live in db/migrations/ and are named NNN_description.sql
where NNN is a zero-padded integer (001, 002, …).

The applied versions are tracked in a `schema_migrations` table.
"""
import logging
import os
import re

import asyncpg

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")

_CREATE_TRACKING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


async def run_migrations(conn: asyncpg.Connection) -> None:
    """Apply all pending migration files in version order."""
    await conn.execute(_CREATE_TRACKING)

    applied: set[int] = {
        row["version"]
        for row in await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
    }

    files = sorted(
        f for f in os.listdir(_MIGRATIONS_DIR) if re.match(r"^\d{3}_.*\.sql$", f)
    )

    for filename in files:
        match = re.match(r"^(\d{3})_", filename)
        if not match:
            continue
        version = int(match.group(1))
        if version in applied:
            continue

        path = os.path.join(_MIGRATIONS_DIR, filename)
        sql = open(path, encoding="utf-8").read()
        logger.info("Applying migration %03d: %s", version, filename)
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
            version, filename,
        )
        logger.info("Migration %03d applied.", version)
