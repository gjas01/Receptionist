"""Verify the voices table loses its UNIQUE(user_id) constraint on a real DB copy."""

import re

from sqlalchemy import inspect, text

from app.db import engine, init_db


def main() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        tables = [r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))]
        print("before tables:", tables)
        if "voices" in tables:
            ddl = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name='voices'")
            ).fetchone()[0]
            print("before DDL:", re.sub(r"\s+", " ", ddl))
            print("before rows:", conn.execute(text("SELECT id, user_id, name FROM voices")).fetchall())

    init_db()

    with engine.begin() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='voices'")
        ).fetchone()[0]
        print("after DDL:", re.sub(r"\s+", " ", ddl))
        print("after rows:", conn.execute(text("SELECT id, user_id, name, voice_id FROM voices")).fetchall())

        # The real test: two voices for the same user.
        conn.execute(
            text(
                "INSERT INTO voices (user_id, name, voice_id, target_model, preferred_name, created_at) "
                "VALUES (2, 'Second Voice', 'qwen-omni-vc-test-second', 'qwen3.8-omni-flash-realtime', "
                "'test_second', '2026-10-19 12:00:00')"
            )
        )
        rows = conn.execute(text("SELECT id, user_id, name FROM voices WHERE user_id=2")).fetchall()
        print("user 2 voices after second insert:", rows)
        assert len(rows) >= 2, "second voice for the same user was not allowed"

        # Referencing slot insert still works after the rebuild.
        vid = rows[-1][0]
        conn.execute(
            text(
                "INSERT INTO voice_slots (user_id, day, start_min, end_min, voice_id) "
                "VALUES (2, 0, 540, 720, :v)"
            ),
            {"v": vid},
        )
        print("slots:", conn.execute(text("SELECT user_id, day, start_min, end_min, voice_id FROM voice_slots")).fetchall())

    # init_db again must be a no-op (idempotent).
    init_db()
    with engine.begin() as conn:
        print("idempotent re-run rows:", conn.execute(text("SELECT count(*) FROM voices")).fetchone()[0])
    print("MIGRATION CHECK PASSED")


if __name__ == "__main__":
    main()
