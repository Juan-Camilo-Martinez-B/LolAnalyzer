"""
Adds authentication columns on databases created before account recovery existed.
create_all() does not alter existing tables.
"""

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_auth_columns(conn: AsyncConnection) -> None:
    await conn.run_sync(_ensure_auth_columns)


def _ensure_auth_columns(sync_conn) -> None:
    inspector = inspect(sync_conn)
    if "users" not in set(inspector.get_table_names()):
        return

    existing = {column["name"] for column in inspector.get_columns("users")}
    timestamp = "TIMESTAMP" if sync_conn.dialect.name == "sqlite" else "TIMESTAMP WITH TIME ZONE"
    additions = {
        "session_version": "INTEGER NOT NULL DEFAULT 1",
        "failed_login_count": "INTEGER NOT NULL DEFAULT 0",
        "locked_until": f"{timestamp} NULL",
        "riot_puuid": "VARCHAR(80) NULL",
        "riot_game_name": "VARCHAR(100) NULL",
        "riot_tag_line": "VARCHAR(20) NULL",
    }
    for name, ddl in additions.items():
        if name not in existing:
            sync_conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))

    index_names = {index["name"] for index in inspector.get_indexes("users")}
    if "uq_users_riot_puuid" not in index_names:
        sync_conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_riot_puuid ON users (riot_puuid)"
        ))
