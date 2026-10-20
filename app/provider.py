"""Provider factory: one realtime session interface, two backends.

`PROVIDER=qwen`   -> Qwen realtime (voice cloning supported on omni models)
`PROVIDER=gemini` -> Gemini Live (30 prebuilt voices, per-session persona shaping)

The engine is chosen per account (User.provider) so the two can be tested side
by side from the GUI; when an account hasn't picked one, the .env PROVIDER
default applies. Which voice answers is decided by `app.schedule`: a named time
slot for the current UK day/time, otherwise a random pick from the user's stored
voices usable by the active engine.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings

log = logging.getLogger(__name__)


def user_provider(user: Any) -> str:
    """Resolved engine for an account: its own choice, else the .env default."""
    settings = get_settings()
    stored = getattr(user, "provider", None)
    if stored:
        return settings.normalize_provider(stored)
    return settings.active_provider


def set_user_provider(user: Any, provider: str | None) -> None:
    user.provider = get_settings().normalize_provider(provider) if provider else None


def create_session(
    on_audio: Any = None,
    on_event: Any = None,
    provider: str | None = None,
) -> Any:
    if provider is None:
        provider = get_settings().active_provider
    if provider == "gemini":
        from app.gemini import GeminiRealtimeSession

        return GeminiRealtimeSession(on_audio=on_audio, on_event=on_event)
    from app.qwen import QwenRealtimeSession

    return QwenRealtimeSession(on_audio=on_audio, on_event=on_event)


def resolve_active_voice(
    db: Session, user: Any, provider: str | None = None
) -> tuple[Any, str]:
    """(Voice row or None, reason) for who should answer right now."""
    from app import schedule

    settings = get_settings()
    provider = provider or user_provider(user)
    target_model = settings.model_for(provider)
    voice, reason = schedule.pick_voice(db, user.id, target_model=target_model)
    if voice is None and reason != "no stored voices":
        return None, f"{reason} (saved for {target_model} of another engine)"
    return voice, reason


def resolve_voice(db: Session, user: Any, provider: str | None = None) -> str:
    """Provider voice identifier to configure the realtime session with."""
    settings = get_settings()
    provider = provider or user_provider(user)
    voice, reason = resolve_active_voice(db, user, provider)
    if voice is not None:
        return voice.voice_id
    if reason != "no stored voices":
        log.info("Falling back to default voice: %s", reason)
    return settings.default_voice_for(provider)


def resolve_delivery(db: Session, user: Any, provider: str | None = None) -> str:
    """Per-voice shaping prompt when saved, else the app-wide default."""
    settings = get_settings()
    voice, _ = resolve_active_voice(db, user, provider)
    if voice and (voice.delivery or "").strip():
        return voice.delivery.strip()
    return settings.qwen_voice_delivery.strip()
