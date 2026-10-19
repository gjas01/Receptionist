"""Provider factory: one realtime session interface, two backends.

`PROVIDER=qwen`   -> Qwen realtime (voice cloning supported on omni models)
`PROVIDER=gemini` -> Gemini Live (30 prebuilt voices, per-session persona shaping)

Which voice answers is decided by `app.schedule`: a named time slot for the
current UK day/time, otherwise a random pick from the user's stored voices.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings


def create_session(
    on_audio: Any = None,
    on_event: Any = None,
) -> Any:
    settings = get_settings()
    if settings.active_provider == "gemini":
        from app.gemini import GeminiRealtimeSession

        return GeminiRealtimeSession(on_audio=on_audio, on_event=on_event)
    from app.qwen import QwenRealtimeSession

    return QwenRealtimeSession(on_audio=on_audio, on_event=on_event)


def resolve_active_voice(db: Session, user: Any) -> tuple[Any, str]:
    """(Voice row or None, reason) for who should answer right now."""
    from app import schedule

    settings = get_settings()
    voice, reason = schedule.pick_voice(db, user.id)
    if voice is not None and voice.target_model != settings.active_model:
        # Cloned/built for a different model or provider — unusable right now.
        return None, f"{reason} (saved for {voice.target_model}, not {settings.active_model})"
    return voice, reason


def resolve_voice(db: Session, user: Any) -> str:
    """Provider voice identifier to configure the realtime session with."""
    settings = get_settings()
    voice, reason = resolve_active_voice(db, user)
    if voice is not None:
        return voice.voice_id
    if reason != "no stored voices":
        import logging

        logging.getLogger(__name__).info("Falling back to default voice: %s", reason)
    return settings.default_voice


def resolve_delivery(db: Session, user: Any) -> str:
    """Per-voice shaping prompt when saved, else the app-wide default."""
    settings = get_settings()
    voice, _ = resolve_active_voice(db, user)
    if voice and (voice.delivery or "").strip():
        return voice.delivery.strip()
    return settings.qwen_voice_delivery.strip()
