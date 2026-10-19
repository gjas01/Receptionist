"""Check the voice_tables migration against a copy of the production DB."""

from sqlalchemy import inspect, text

from app.db import engine, init_db


def main() -> None:
    with engine.begin() as conn:
        tables = [r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))]
        print("before tables:", tables)
        if "voice_clones" in tables:
            print("before clones:", conn.execute(text("SELECT id, user_id, voice_id, target_model FROM voice_clones")).fetchall())
        print("before users:", conn.execute(text("SELECT id, email FROM users")).fetchall())

    init_db()

    insp = inspect(engine)
    print("after tables:", insp.get_table_names())
    print("voices cols:", [c["name"] for c in insp.get_columns("voices")])
    print("voices indexes:", [(i["name"], i["unique"], i["column_names"]) for i in insp.get_indexes("voices")])
    print("slot cols:", [c["name"] for c in insp.get_columns("voice_slots")])

    with engine.begin() as conn:
        print("voices rows:", conn.execute(text("SELECT id, user_id, name, voice_id, target_model FROM voices")).fetchall())

    # Exercise the schedule logic.
    from app import schedule
    from app.models import User

    users = engine.connect().execute(text("SELECT id FROM users")).fetchall()
    if not users:
        print("no users; skipping resolver check")
        return
    uid = users[0][0]
    slots = [
        schedule.Slot(None, None, 0 * 60, 9 * 60, None),          # All days 00:00-09:00 random
        schedule.Slot(None, 0, 9 * 60, 12 * 60, None),            # Mon 09:00-12:00
    ]
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        saved = schedule.save_slots(db, uid, slots)
        print("saved slots:", [s.as_row() for s in saved])
        from datetime import datetime
        import zoneinfo

        tz = zoneinfo.ZoneInfo("Europe/London")
        for day, hh, mm in [(0, 10, 0), (1, 10, 0), (0, 3, 0), (0, 13, 0)]:
            when = datetime(2026, 10, 19 + day, hh, mm, tzinfo=tz)
            got = schedule.active_slot(db, uid, when)
            print(f"active_slot day={day} {hh:02d}:{mm:02d} ->", got.as_row() if got else None)
        v, reason = schedule.pick_voice(db, uid)
        print("pick_voice:", getattr(v, "voice_id", None), reason)

    # Overlap rejection
    try:
        schedule.save_slots(
            Session(engine),
            uid,
            [
                schedule.Slot(None, 0, 9 * 60, 13 * 60, None),
                schedule.Slot(None, 0, 12 * 60, 15 * 60, None),
            ],
        )
        print("OVERLAP NOT REJECTED - BUG")
    except ValueError as exc:
        print("overlap rejected as expected:", exc)

    # All-days vs specific day clash
    try:
        schedule.save_slots(
            Session(engine),
            uid,
            [
                schedule.Slot(None, None, 9 * 60, 13 * 60, None),
                schedule.Slot(None, 2, 12 * 60, 15 * 60, None),
            ],
        )
        print("ALL/DAY OVERLAP NOT REJECTED - BUG")
    except ValueError as exc:
        print("all-days overlap rejected as expected:", exc)


if __name__ == "__main__":
    main()
