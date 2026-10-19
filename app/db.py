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
       plus a user-assigned ``name``). The unique index is dropped before the
       rename so a user can hold several named voices.
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
        insp = inspect(engine)
        for idx in insp.get_indexes("voice_clones"):
            if idx.get("unique"):
                with engine.begin() as conn:
                    conn.execute(text(f'DROP INDEX IF EXISTS "{idx["name"]}"'))
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("voice_clones")}
        with engine.begin() as conn:
            if "delivery" not in cols:
                conn.execute(text("ALTER TABLE voice_clones ADD COLUMN delivery TEXT"))
            if "name" not in cols:
                conn.execute(text("ALTER TABLE voice_clones ADD COLUMN name VARCHAR(80)"))
            conn.execute(text("ALTER TABLE voice_clones RENAME TO voices"))
