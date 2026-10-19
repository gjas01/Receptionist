from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    future=True,
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    # SQLite defaults to a rollback journal; WAL survives abrupt kills (docker
    # restart, host reboot) without leaving the DB corrupt or locked.
    if dbapi_connection.__class__.__name__ == "Connection":  # pysqlite
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    _migrate_voice_tables()
    Base.metadata.create_all(bind=engine)


def _migrate_voice_tables() -> None:
    """Idempotent, hand-rolled migrations for the voice tables.

    1. users.company_name / users.settings columns (added after launch).
    2. voice_clones (one row per user, UNIQUE user_id) -> voices (many per user,
       plus a user-assigned ``name``).

    The legacy UNIQUE on ``user_id`` lives inside the table definition as well as
    in an index, so renaming the table is not enough — SQLite cannot drop a
    column constraint in place. We rebuild the table without it when needed.
    """
    insp = inspect(engine)
    tables = set(insp.get_table_names())

    if "users" in tables:
        cols = {c["name"] for c in insp.get_columns("users")}
        with engine.begin() as conn:
            if "company_name" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN company_name VARCHAR(255)"))
            if "settings" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN settings TEXT"))

    if "voice_clones" in tables and "voices" not in tables:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE voice_clones RENAME TO voices"))

    if "voices" in inspect(engine).get_table_names():
        _ensure_voices_schema()


VOICE_COLUMNS = (
    "id",
    "user_id",
    "name",
    "voice_id",
    "target_model",
    "preferred_name",
    "delivery",
    "created_at",
)


def _ensure_voices_schema() -> None:
    """Add the `name` column and drop the UNIQUE user_id constraint if present."""
    import re as _re

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("voices")}
    with engine.begin() as conn:
        if "name" not in cols:
            conn.execute(text("ALTER TABLE voices ADD COLUMN name VARCHAR(80)"))

        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='voices'")
        ).fetchone()
        create_sql = (row[0] or "") if row else ""

        # Inline column constraint ("user_id INTEGER NOT NULL UNIQUE") or a
        # table constraint ("UNIQUE (user_id)"). Auto-indexes are not reported
        # by SQLAlchemy, so we match on the DDL text itself.
        nospace = create_sql.upper().replace(" ", "")
        has_unique_user = "UNIQUE(USER_ID)" in nospace or _re.search(
            r"\buser_id\b[^,)]*\bunique\b", create_sql, _re.IGNORECASE
        )
        if not has_unique_user:
            return

        # Rebuild the table without the constraint. (SQLite has no DROP
        # CONSTRAINT; FK enforcement is off on this driver by default, so a
        # plain drop+rename is safe and voice_slots keeps referencing `voices`
        # by name.)
        conn.execute(text("DROP TABLE IF EXISTS voices_rebuild"))
        conn.execute(
            text(
                """
                CREATE TABLE voices_rebuild (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    voice_id VARCHAR(255) NOT NULL,
                    target_model VARCHAR(128) NOT NULL,
                    preferred_name VARCHAR(32) NOT NULL,
                    delivery TEXT,
                    created_at DATETIME NOT NULL,
                    name VARCHAR(80)
                )
                """
            )
        )
        conn.execute(
            text(
                f"INSERT INTO voices_rebuild ({', '.join(VOICE_COLUMNS)}) "
                f"SELECT {', '.join(VOICE_COLUMNS)} FROM voices"
            )
        )
        conn.execute(text("DROP TABLE voices"))
        conn.execute(text("ALTER TABLE voices_rebuild RENAME TO voices"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_voices_user_id ON voices (user_id)"))

