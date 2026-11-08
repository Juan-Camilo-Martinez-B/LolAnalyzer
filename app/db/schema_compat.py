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
        "theme_preference": "VARCHAR(20) NOT NULL DEFAULT 'light'",
    }
    for name, ddl in additions.items():
        if name not in existing:
            sync_conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))

    # One Riot ID can be linked to several analyzer accounts (migration 0009).
    index_names = {index["name"] for index in inspector.get_indexes("users")}
    if "uq_users_riot_puuid" in index_names:
        sync_conn.execute(text("DROP INDEX IF EXISTS uq_users_riot_puuid"))
    if "ix_users_riot_puuid" not in index_names:
        sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_riot_puuid ON users (riot_puuid)"))

    _ensure_match_profile_column(sync_conn, inspector)


def _ensure_match_profile_column(sync_conn, inspector) -> None:
    """Each stored game keeps its League account; older games get the linked one."""
    if "local_match_history" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("local_match_history")}
    if "riot_puuid" in existing:
        return
    sync_conn.execute(text("ALTER TABLE local_match_history ADD COLUMN riot_puuid VARCHAR(80) NULL"))
    sync_conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_local_match_user_puuid ON local_match_history (user_id, riot_puuid)"
    ))
    sync_conn.execute(text(
        "UPDATE local_match_history SET riot_puuid = "
        "(SELECT riot_puuid FROM users WHERE users.id = local_match_history.user_id) "
        "WHERE riot_puuid IS NULL"
    ))
