from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.db import get_db
from app import schedule
from app.models import User, Voice, VoiceSlot
from app.provider import user_provider
from app.qwen import create_cloned_voice, preferred_voice_name

log = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
}
MAX_NAME_LEN = 80


class SlotPayload(BaseModel):
    day: int | None = None  # 0=Mon..6=Sun, null=every day
    start: str  # HH:MM
    end: str  # HH:MM
    voice_id: int | None = None  # null = random


class SlotsRequest(BaseModel):
    slots: list[SlotPayload]


def _voice_row(v: Voice, provider: str | None = None) -> dict:
    settings = get_settings()
    provider = provider or settings.active_provider
    return {
        "id": v.id,
        "name": v.name,
        "voice_id": v.voice_id,
        "target_model": v.target_model,
        "delivery": v.delivery or "",
        "for_active_model": v.target_model == settings.model_for(provider),
    }


def _user_voices(db: Session, user: User) -> list[Voice]:
    return (
        db.query(Voice)
        .filter(Voice.user_id == user.id)
        .order_by(Voice.created_at, Voice.id)
        .all()
    )


@router.get("/api/voices")
async def list_voices(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Please log in first."}, status_code=401)
    settings = get_settings()
    provider = user_provider(user)
    target_model = settings.model_for(provider)
    voices = _user_voices(db, user)
    slots = schedule.load_slots(db, user.id)
    active, reason = schedule.pick_voice(db, user.id, target_model=target_model)
    return JSONResponse(
        {
            "voices": [_voice_row(v, provider) for v in voices],
            "slots": [s.as_row() for s in slots],
            "now": {"day": schedule.uk_now().weekday(), "time": schedule.uk_now().strftime("%H:%M")},
            "active_voice_id": active.id if active else None,
            "reason": reason,
            "active_model": target_model,
            "provider": provider,
            "day_names": schedule.DAY_NAMES,
        }
    )


@router.post("/api/voice/preset")
async def set_preset_voice(
    request: Request,
    voice_name: str = Form(...),
    name: str = Form(""),
    delivery: str = Form(""),
    db: Session = Depends(get_db),
):
    """Save a Gemini prebuilt voice (with a friendly label + shaping prompt)."""
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Please log in first."}, status_code=401)

    settings = get_settings()
    if user_provider(user) != "gemini":
        return JSONResponse(
            {"error": "Preset voice selection is only for the Gemini engine. Switch engine first."},
            status_code=400,
        )
    from app.gemini import GEMINI_VOICES

    valid = {v["name"] for v in GEMINI_VOICES}
    if voice_name not in valid:
        return JSONResponse({"error": "Unknown voice name."}, status_code=400)

    label = (name or voice_name).strip()[:MAX_NAME_LEN]
    # Re-saving the same preset updates it in place; otherwise add a new voice.
    voice = next(
        (
            v
            for v in _user_voices(db, user)
            if v.voice_id == voice_name and v.preferred_name == "preset"
        ),
        None,
    )
    if voice is None:
        voice = Voice(user_id=user.id)
        db.add(voice)
    voice.name = label
    voice.voice_id = voice_name
    voice.target_model = settings.gemini_model
    voice.preferred_name = "preset"
    voice.delivery = delivery.strip()[:1000] or None
    db.commit()

    return JSONResponse({"voice": _voice_row(voice, "gemini")})


@router.post("/api/voice/clone")
async def clone_voice(
    request: Request,
    audio: UploadFile = File(...),
    full_name: str = Form(""),
    name: str = Form(""),
    delivery: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Please log in first."}, status_code=401)

    settings = get_settings()
    if user_provider(user) == "gemini":
        return JSONResponse(
            {
                "error": "Gemini Live uses its 30 built-in voices; microphone cloning is "
                "only available on the Qwen engine. Switch engine first, or pick a voice from the list."
            },
            status_code=400,
        )
    if not settings.qwen_configured:
        return JSONResponse(
            {"error": "DASHSCOPE_KEY and API_ENDPOINT are not set in the .env file."},
            status_code=400,
        )

    person_name = (full_name or user.full_name or "").strip()
    if not person_name:
        return JSONResponse({"error": "Enter your name so the cloned greeting can address you."}, status_code=400)

    content_type = (audio.content_type or "audio/wav").split(";")[0].strip().lower()
    if content_type not in ALLOWED_TYPES:
        content_type = "audio/wav"

    audio_bytes = await audio.read()
    if len(audio_bytes) < 1000:
        return JSONResponse({"error": "The recording is too short. Please try again."}, status_code=400)
    if len(audio_bytes) > 10 * 1024 * 1024:
        return JSONResponse({"error": "The recording must be smaller than 10 MB."}, status_code=400)

    preferred = preferred_voice_name(person_name)
    try:
        voice_id = await create_cloned_voice(audio_bytes, content_type, preferred)
    except Exception as exc:
        log.exception("Voice cloning failed")
        return JSONResponse({"error": str(exc)}, status_code=502)

    user.full_name = person_name
    voice = Voice(
        user_id=user.id,
        name=(name or person_name).strip()[:MAX_NAME_LEN],
        voice_id=voice_id,
        target_model=settings.qwen_model,
        preferred_name=preferred,
        delivery=delivery.strip()[:1000] or None,
    )
    db.add(voice)
    db.commit()

    phrase = (
        f"Hi {person_name}, This is your cloned voice. "
        "This is how your phone will be answered in future."
    )
    return JSONResponse(
        {
            "voice_id": voice_id,
            "target_model": settings.qwen_model,
            "preview_phrase": phrase,
            "voice": _voice_row(voice, "qwen"),
        }
    )


@router.post("/api/voice/{voice_id}/update")
async def update_voice(
    request: Request,
    voice_id: int,
    name: str = Form(None),
    delivery: str = Form(None),
    db: Session = Depends(get_db),
):
    """Rename a stored voice (and optionally change its shaping prompt)."""
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Please log in first."}, status_code=401)
    voice = db.query(Voice).filter(Voice.id == voice_id, Voice.user_id == user.id).first()
    if not voice:
        return JSONResponse({"error": "Voice not found."}, status_code=404)
    if name is not None:
        label = name.strip()[:MAX_NAME_LEN]
        if not label:
            return JSONResponse({"error": "The voice name cannot be empty."}, status_code=400)
        voice.name = label
    if delivery is not None:
        voice.delivery = delivery.strip()[:1000] or None
    db.commit()
    return JSONResponse({"voice": _voice_row(voice, user_provider(user))})


@router.post("/api/voice/{voice_id}/delete")
async def delete_voice(
    request: Request,
    voice_id: int,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Please log in first."}, status_code=401)
    voice = db.query(Voice).filter(Voice.id == voice_id, Voice.user_id == user.id).first()
    if not voice:
        return JSONResponse({"error": "Voice not found."}, status_code=404)
    # Slots that referenced this voice fall back to random rather than vanishing.
    db.query(VoiceSlot).filter(
        VoiceSlot.user_id == user.id,
        VoiceSlot.voice_id == voice_id,
    ).update({VoiceSlot.voice_id: None}, synchronize_session=False)
    db.delete(voice)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/voice/slots")
async def save_schedule(request: Request, db: Session = Depends(get_db)):
    """Replace the answering schedule; rejects overlapping slots."""
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Please log in first."}, status_code=401)
    try:
        payload = SlotsRequest.model_validate(json.loads(await request.body() or b"{}"))
    except Exception:
        return JSONResponse({"error": "Invalid schedule payload."}, status_code=400)

    slots: list[schedule.Slot] = []
    for p in payload.slots:
        if p.day is not None and not (0 <= p.day <= 6):
            return JSONResponse({"error": "Day must be 0-6 or null for All."}, status_code=400)
        try:
            start = schedule.parse_hhmm(p.start)
            end = schedule.parse_hhmm(p.end)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if p.voice_id is not None:
            exists = (
                db.query(Voice)
                .filter(Voice.id == p.voice_id, Voice.user_id == user.id)
                .first()
            )
            if not exists:
                return JSONResponse({"error": "Schedule refers to an unknown voice."}, status_code=400)
        slots.append(schedule.Slot(None, p.day, start, end, p.voice_id))

    try:
        saved = schedule.save_slots(db, user.id, slots)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return JSONResponse({"slots": [s.as_row() for s in saved]})
