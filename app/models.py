from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Per-account realtime engine: "qwen" or "gemini". None = use the .env default.
    provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    phone: Mapped["PhoneNumber | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    voices: Mapped[list["Voice"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    voice_slots: Mapped[list["VoiceSlot"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    owner_name: Mapped[str] = mapped_column(String(255))
    twilio_account_sid: Mapped[str] = mapped_column(String(64))
    twilio_auth_token: Mapped[str] = mapped_column(String(64))
    twilio_phone_number: Mapped[str] = mapped_column(String(32), index=True)
    webhook_registered: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="phone")


class Voice(Base):
    """A named voice available to a user: a Qwen clone or a Gemini preset."""

    __tablename__ = "voices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    voice_id: Mapped[str] = mapped_column(String(255))
    target_model: Mapped[str] = mapped_column(String(128))
    preferred_name: Mapped[str] = mapped_column(String(32))
    # Per-voice shaping/delivery prompt; blank falls back to the app default.
    delivery: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="voices")


class VoiceSlot(Base):
    """One answering-schedule row: day (None = every day), UK time range, and the
    voice to use (None = random). Uncovered periods fall back to random."""

    __tablename__ = "voice_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Mon..6=Sun, None=All
    start_min: Mapped[int] = mapped_column(Integer)  # minutes after midnight, UK time
    end_min: Mapped[int] = mapped_column(Integer)
    voice_id: Mapped[int | None] = mapped_column(ForeignKey("voices.id"), nullable=True)

    user: Mapped[User] = relationship(back_populates="voice_slots")
    voice: Mapped["Voice | None"] = relationship("Voice")
