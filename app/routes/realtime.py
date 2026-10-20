from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.audio_convert import RateState, mulaw8k_to_pcm16k, pcm24k_to_mulaw8k
from app.call_guard import CallGuard
from app.config import get_settings
from app.db import SessionLocal
from app.greeting import receptionist_greeting, receptionist_instructions
from app.models import PhoneNumber, User
from app.provider import (
    create_session,
    resolve_active_voice,
    resolve_delivery,
    resolve_voice,
    user_provider,
)
from app.twilio_util import hangup_call

log = logging.getLogger(__name__)
router = APIRouter()


def _vad() -> dict[str, Any]:
    """Server VAD tuned against background noise; overridable via .env."""
    s = get_settings()
    return {
        "type": "server_vad",
        "threshold": s.vad_threshold,
        "silence_duration_ms": s.vad_silence_duration_ms,
        "prefix_padding_ms": s.vad_prefix_padding_ms,
        # Noise that briefly trips the VAD must not cut off the greeting or
        # an answer already in progress.
        "interrupt_response": False,
    }


def _user_from_session(websocket: WebSocket, db: Session) -> User | None:
    user_id = websocket.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


async def _send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(payload))


@router.websocket("/ws/voice-preview")
async def voice_preview(websocket: WebSocket):
    await websocket.accept()
    db = SessionLocal()
    qwen: Any | None = None
    try:
        user = _user_from_session(websocket, db)
        if not user:
            await _send_json(websocket, {"type": "error", "message": "Please log in first."})
            return
        name = user.full_name or (user.phone.owner_name if user.phone else None) or "there"
        phrase = (
            f"Hi {name}, This is your cloned voice. "
            "This is how your phone will be answered in future."
        )
        done = asyncio.Event()

        async def on_audio(delta_b64: str) -> None:
            await _send_json(websocket, {"type": "audio", "data": delta_b64, "sample_rate": 24000})

        async def on_event(event: dict[str, Any]) -> None:
            event_type = event.get("type")
            if event_type == "response.audio_transcript.done":
                await _send_json(websocket, {"type": "transcript", "text": event.get("transcript", "")})
            elif event_type == "response.done":
                done.set()
            elif event_type == "error":
                message = (event.get("error") or {}).get("message") or "Qwen returned an error."
                await _send_json(websocket, {"type": "error", "message": message})
                done.set()

        # Optional ?voice=Name & ?delivery=Text queries let the UI audition
        # a preset/shape before saving. Saved per-voice delivery wins over the app default.
        provider = user_provider(user)
        override = websocket.query_params.get("voice")
        voice = override.strip() if override and override.strip() else resolve_voice(db, user, provider)
        q_delivery = (websocket.query_params.get("delivery") or "").strip()
        if override and override.strip():
            active_voice = None
        else:
            active_voice, _ = resolve_active_voice(db, user, provider)
        stored = ((active_voice.delivery if active_voice else "") or "").strip()
        delivery = q_delivery or stored or get_settings().qwen_voice_delivery.strip()
        qwen = create_session(on_audio=on_audio, on_event=on_event, provider=provider)
        await qwen.connect()
        await qwen.configure(
            voice=voice,
            instructions=(
                "You are previewing the receptionist voice. Speak the requested sentence "
                "naturally, then stop. Do not add extra commentary."
                + (f" Delivery: {delivery}." if delivery else "")
            ),
            turn_detection=None,
        )
        await _send_json(websocket, {"type": "status", "message": "Playing cloned voice…"})
        await qwen.request_response(
            instructions=f'Say exactly this sentence and nothing else: "{phrase}"'
        )
        try:
            await asyncio.wait_for(done.wait(), timeout=45)
        except TimeoutError:
            await _send_json(websocket, {"type": "error", "message": "Timed out waiting for cloned-voice playback."})
        else:
            await _send_json(websocket, {"type": "done"})
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=2)
            except TimeoutError:
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("Voice preview failed")
        try:
            await _send_json(websocket, {"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if qwen:
            await qwen.close()
        db.close()
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/ws/test-call")
async def test_call(websocket: WebSocket):
    await websocket.accept()
    db = SessionLocal()
    qwen: Any | None = None
    try:
        user = _user_from_session(websocket, db)
        if not user:
            await _send_json(websocket, {"type": "error", "message": "Please log in first."})
            return
        if not websocket.session.get("test_mode"):
            await _send_json(websocket, {"type": "error", "message": "Switch to test mode first."})
            return

        provider = user_provider(user)
        greeting = receptionist_greeting(user.business_name, user.full_name)
        active_voice, why = resolve_active_voice(db, user, provider)
        log.info(
            "Test call answering (provider=%s) with voice=%s (%s)",
            provider,
            getattr(active_voice, "name", None) or "default",
            why,
        )
        instructions = receptionist_instructions(
            user.business_name,
            user.full_name,
            greeting,
            delivery=resolve_delivery(db, user, provider),
        )

        async def on_audio(delta_b64: str) -> None:
            if guard:
                guard.track_audio(delta_b64)
            await _send_json(websocket, {"type": "audio", "data": delta_b64, "sample_rate": 24000})

        guard: CallGuard | None = None

        async def on_event(event: dict[str, Any]) -> None:
            if guard:
                guard.handle_event(event)
            event_type = event.get("type")
            if event_type == "response.audio_transcript.delta":
                await _send_json(websocket, {"type": "agent_delta", "text": event.get("delta", "")})
            elif event_type == "response.audio_transcript.done":
                await _send_json(websocket, {"type": "agent", "text": event.get("transcript", "")})
            elif event_type == "conversation.item.input_audio_transcription.completed":
                await _send_json(websocket, {"type": "caller", "text": event.get("transcript", "")})
            elif event_type == "error":
                message = (event.get("error") or {}).get("message") or "Qwen returned an error."
                await _send_json(websocket, {"type": "error", "message": message})

        async def hangup_test() -> None:
            try:
                await _send_json(websocket, {"type": "ended", "reason": "silence"})
                await websocket.close()
            except Exception:
                pass

        qwen = create_session(on_audio=on_audio, on_event=on_event, provider=provider)
        await qwen.connect()
        await qwen.configure(
            voice=resolve_voice(db, user, provider), instructions=instructions, turn_detection=_vad()
        )
        guard = CallGuard(
            qwen, idle_seconds=get_settings().call_idle_seconds, hangup=hangup_test
        )
        guard.start()
        await _send_json(websocket, {"type": "ready", "greeting": greeting})
        await qwen.request_response(
            instructions=f'Say exactly this greeting, then wait for the caller: "{greeting}"'
        )

        while True:
            if guard.ended.is_set():
                break
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=0.5)
            except TimeoutError:
                continue
            if message.get("type") == "websocket.disconnect":
                break
            raw = message.get("text")
            if not raw:
                continue
            data = json.loads(raw)
            msg_type = data.get("type")
            if msg_type == "audio" and data.get("data"):
                await qwen.append_audio(data["data"])
            elif msg_type in ("hangup", "stop"):
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("Test call failed")
        try:
            await _send_json(websocket, {"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if guard:
            await guard.stop()
        if qwen:
            await qwen.close()
        db.close()


@router.websocket("/ws/twilio")
async def twilio_media(websocket: WebSocket):
    """Bridge Twilio Media Streams (mulaw 8kHz) to Qwen realtime (PCM 16kHz in / 24kHz out)."""
    await websocket.accept()
    db = SessionLocal()
    qwen: Any | None = None
    stream_sid = None
    to_pcm = RateState()
    to_mulaw = RateState()
    try:
        start_event = None
        while start_event is None:
            raw = await websocket.receive_text()
            event = json.loads(raw)
            if event.get("event") == "start":
                start_event = event
            elif event.get("event") == "stop":
                return

        start = start_event.get("start") or {}
        stream_sid = start.get("streamSid") or start_event.get("streamSid")
        call_sid = start.get("callSid") or start_event.get("callSid") or ""
        custom = start.get("customParameters") or {}
        to_number = custom.get("to") or ""
        phone = (
            db.query(PhoneNumber).filter(PhoneNumber.twilio_phone_number == to_number).first()
            if to_number
            else None
        )
        user = phone.user if phone else None
        if not user:
            log.error("Twilio stream has no account for %s", to_number)
            return

        provider = user_provider(user)
        greeting = receptionist_greeting(user.business_name, user.full_name)
        active_voice, why = resolve_active_voice(db, user, provider)
        log.info(
            "Live call answering %s (provider=%s) with voice=%s (%s)",
            to_number,
            provider,
            getattr(active_voice, "name", None) or "default",
            why,
        )
        instructions = receptionist_instructions(
            user.business_name,
            user.full_name,
            greeting,
            delivery=resolve_delivery(db, user, provider),
        )
        outbound_q: asyncio.Queue[str] = asyncio.Queue()

        async def on_audio(delta_b64: str) -> None:
            if guard:
                guard.track_audio(delta_b64)
            pcm24k = base64.b64decode(delta_b64)
            mulaw = pcm24k_to_mulaw8k(pcm24k, to_mulaw)
            if not mulaw:
                return
            await outbound_q.put(base64.b64encode(mulaw).decode("ascii"))

        async def pump_outbound() -> None:
            while True:
                payload = await outbound_q.get()
                if payload is None:
                    return
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": payload},
                        }
                    )
                )

        guard: CallGuard | None = None

        async def on_event(event: dict[str, Any]) -> None:
            if guard:
                guard.handle_event(event)

        async def hangup_twilio() -> None:
            """End the live call: REST hangup + close the media stream socket."""
            try:
                await asyncio.to_thread(
                    hangup_call,
                    phone.twilio_account_sid,
                    phone.twilio_auth_token,
                    call_sid,
                )
            except Exception:
                log.debug("Twilio REST hangup failed for %s", call_sid, exc_info=True)
            try:
                await websocket.close()
            except Exception:
                pass

        qwen = create_session(on_audio=on_audio, on_event=on_event, provider=provider)
        await qwen.connect()
        await qwen.configure(
            voice=resolve_voice(db, user, provider), instructions=instructions, turn_detection=_vad()
        )
        guard = CallGuard(
            qwen, idle_seconds=get_settings().call_idle_seconds, hangup=hangup_twilio
        )
        await qwen.request_response(
            instructions=f'Say exactly this greeting, then wait for the caller: "{greeting}"'
        )
        guard.start()
        pump_task = asyncio.create_task(pump_outbound())

        try:
            while True:
                if guard.ended.is_set():
                    break
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                except TimeoutError:
                    continue
                event = json.loads(raw)
                event_type = event.get("event")
                if event_type == "media":
                    payload = (event.get("media") or {}).get("payload")
                    if not payload:
                        continue
                    mulaw = base64.b64decode(payload)
                    pcm16k = mulaw8k_to_pcm16k(mulaw, to_pcm)
                    if pcm16k:
                        await qwen.append_audio(base64.b64encode(pcm16k).decode("ascii"))
                elif event_type in ("stop", "closed"):
                    break
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Twilio media bridge failed")
    finally:
        if guard:
            await guard.stop()
        if qwen:
            await qwen.close()
        db.close()
