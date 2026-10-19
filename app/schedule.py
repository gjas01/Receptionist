"""Which voice answers right now.

A slot is (day, start, end, voice). ``day=None`` means every day. Voice ``None``
means "random pick from the user's named voices". Uncovered time is random too.
"""

from __future__ import annotations

import random
import zoneinfo
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app import models

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MIN_PER_DAY = 24 * 60

_uk = zoneinfo.ZoneInfo("Europe/London")


def uk_now() -> datetime:
    return datetime.now(_uk)


@dataclass
class Slot:
    id: int | None
    day: int | None  # 0=Mon..6=Sun, None=All
    start_min: int
    end_min: int
    voice_id: int | None

    def as_row(self) -> dict:
        return {
            "id": self.id,
            "day": self.day,
            "start": _fmt(self.start_min),
            "end": _fmt(self.end_min),
            "voice_id": self.voice_id,
        }


def _fmt(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def parse_hhmm(value: str) -> int:
    parts = str(value).strip().split(":")
    if len(parts) != 2:
        raise ValueError("times must be HH:MM")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("time out of range")
    return hh * 60 + mm


def overlaps(a: Slot, b: Slot) -> bool:
    """True if two slots share any instant. `All` clashes with every day."""
    if a.day is not None and b.day is not None and a.day != b.day:
        return False
    return a.start_min < b.end_min and b.start_min < a.end_min


def validate_slots(slots: list[Slot]) -> None:
    """Raise ValueError on empty/bad ranges or overlapping slots."""
    seen: list[Slot] = []
    for s in slots:
        if s.end_min <= s.start_min:
            raise ValueError(
                f"{_day_label(s.day)} {_fmt(s.start_min)}–{_fmt(s.end_min)}: "
                "end time must be after start time"
            )
        for prev in seen:
            if overlaps(s, prev):
                raise ValueError(
                    f"{_day_label(s.day)} {_fmt(s.start_min)}–{_fmt(s.end_min)} overlaps "
                    f"{_day_label(prev.day)} {_fmt(prev.start_min)}–{_fmt(prev.end_min)}"
                )
        seen.append(s)


def _day_label(day: int | None) -> str:
    return "All days" if day is None else DAY_NAMES[day]


def load_slots(db: Session, user_id: int) -> list[Slot]:
    rows = (
        db.query(models.VoiceSlot)
        .filter(models.VoiceSlot.user_id == user_id)
        .order_by(models.VoiceSlot.day.isnot(None), models.VoiceSlot.start_min)
        .all()
    )
    return [
        Slot(r.id, r.day, r.start_min, r.end_min, r.voice_id)
        for r in rows
    ]


def save_slots(db: Session, user_id: int, slots: list[Slot]) -> list[Slot]:
    """Replace the user's schedule wholesale after validating it."""
    validate_slots(slots)
    db.query(models.VoiceSlot).filter(models.VoiceSlot.user_id == user_id).delete()
    for s in slots:
        db.add(
            models.VoiceSlot(
                user_id=user_id,
                day=s.day,
                start_min=s.start_min,
                end_min=s.end_min,
                voice_id=s.voice_id,
            )
        )
    db.commit()
    return load_slots(db, user_id)


def active_slot(db: Session, user_id: int, when: datetime | None = None) -> Slot | None:
    """The most specific slot covering ``when`` (exact day beats All)."""
    when = when or uk_now()
    minute = when.hour * 60 + when.minute
    weekday = when.weekday()
    best: Slot | None = None
    for s in load_slots(db, user_id):
        if not (s.start_min <= minute < s.end_min):
            continue
        if s.day is not None and s.day != weekday:
            continue
        if best is None or (best.day is None and s.day is not None):
            best = s
    return best


def pick_voice(db: Session, user_id: int, when: datetime | None = None):
    """Return (voice, reason) for who should answer now.

    Voices saved for a different model than the active one are ignored — the
    scheduler degrades gracefully (slot voice -> random -> none).
    voice may be None (nothing usable -> caller falls back to provider default).
    """
    from app.config import get_settings

    active_model = get_settings().active_model

    def usable(v):
        return v is not None and v.target_model == active_model

    slot = active_slot(db, user_id, when)
    if slot is not None and slot.voice_id is not None:
        v = (
            db.query(models.Voice)
            .filter(models.Voice.id == slot.voice_id, models.Voice.user_id == user_id)
            .first()
        )
        if usable(v):
            return v, f"schedule: {_day_label(slot.day)} {_fmt(slot.start_min)}-{_fmt(slot.end_min)}"
    voices = (
        db.query(models.Voice)
        .filter(
            models.Voice.user_id == user_id,
            models.Voice.target_model == active_model,
        )
        .order_by(models.Voice.created_at)
        .all()
    )
    if not voices:
        return None, "no stored voices"
    return random.choice(voices), "random"
