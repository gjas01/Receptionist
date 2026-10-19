from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import websockets

from app.config import get_settings

log = logging.getLogger(__name__)

AudioHandler = Callable[[str], Awaitable[None]]
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

PREFERRED_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")


def preferred_voice_name(full_name: str | None) -> str:
    base = PREFERRED_NAME_RE.sub("_", (full_name or "host").strip())[:8].strip("_")
    if not base:
        base = "host"
    suffix = uuid.uuid4().hex[:4]
    name = f"{base}_{suffix}"
    return name[:16]


def clone_payload(family: str, target_model: str, preferred_name: str, data_uri: str) -> dict[str, Any]:
    """Build the enrollment request for the active model family.

    Qwen-Omni uses `qwen-voice-enrollment` with an inline base64 data URI.
    Qwen-Audio-Realtime uses `voice-enrollment` with a URL and a name prefix.
    """
    if family == "audio":
        return {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": target_model,
                "prefix": preferred_name,
                "url": data_uri,
                "language_hints": ["en"],
            },
        }
    return {
        "model": "qwen-voice-enrollment",
        "parameters": {"voice_clone_mode": "normal"},
        "input": {
            "action": "create",
            "target_model": target_model,
            "preferred_name": preferred_name,
            "audio": {"data": data_uri},
        },
    }


async def create_cloned_voice(
    audio_bytes: bytes,
    mime_type: str,
    preferred_name: str,
) -> str:
    settings = get_settings()
    if not settings.qwen_configured:
        raise RuntimeError("DASHSCOPE_KEY and API_ENDPOINT must be set in the .env file.")

    import base64

    data_uri = f"data:{mime_type};base64,{base64.b64encode(audio_bytes).decode('ascii')}"
    payload = clone_payload(
        settings.qwen_family, settings.qwen_model, preferred_name, data_uri
    )
    headers = {
        "Authorization": f"Bearer {settings.dashscope_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(settings.voice_clone_url, headers=headers, json=payload)

    if response.status_code != 200:
        text = response.text[:800]
        if "preprocess service not found" in text:
            raise RuntimeError(
                f"Voice cloning is not provisioned for {settings.qwen_model} on this account. "
                "The receptionist will answer with the default voice. "
                "Set QWEN_MODEL to a model with cloning enabled (for example "
                "qwen3.5-omni-plus-realtime) to clone your own voice."
            )
        if "DurationLimitError" in text or "duration" in text.lower():
            raise RuntimeError(
                "The recording is too long for cloning. Use a clip of about 20-60 seconds "
                "of clear speech."
            )
        raise RuntimeError(f"Voice cloning failed ({response.status_code}): {text}")
    try:
        output = response.json()["output"]
        voice = output.get("voice") or output.get("voice_id")
    except (KeyError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Unexpected cloning response: {response.text[:800]}") from exc
    if not voice:
        raise RuntimeError("Voice cloning did not return a voice identifier.")
    return voice


def resolve_voice(user: Any) -> str:
    """Pick the cloned voice when it matches the active model, else the preset voice."""
    settings = get_settings()
    clone = getattr(user, "voice", None)
    if clone and clone.voice_id and clone.target_model == settings.qwen_model:
        return clone.voice_id
    return settings.default_voice


class QwenRealtimeSession:
    """WebSocket client for the configured Qwen realtime model."""

    def __init__(
        self,
        on_audio: AudioHandler | None = None,
        on_event: EventHandler | None = None,
    ) -> None:
        self.on_audio = on_audio
        self.on_event = on_event
        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._session_ready = asyncio.Event()
        self._session_updated = asyncio.Event()
        self._error_event = asyncio.Event()
        self._closed = asyncio.Event()
        self._error: str | None = None

    async def connect(self) -> None:
        settings = get_settings()
        if not settings.qwen_configured:
            raise RuntimeError("DASHSCOPE_KEY and API_ENDPOINT must be set in the .env file.")

        headers = {"Authorization": f"Bearer {settings.dashscope_key}"}
        url = settings.realtime_ws_url
        log.info("Connecting to Qwen realtime: %s", url)
        try:
            self._ws = await websockets.connect(url, additional_headers=headers, max_size=8 * 1024 * 1024)
        except TypeError:
            self._ws = await websockets.connect(url, extra_headers=headers, max_size=8 * 1024 * 1024)

        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await asyncio.wait_for(self._session_ready.wait(), timeout=15)
        except TimeoutError as exc:
            await self.close()
            raise RuntimeError("Timed out waiting for Qwen session.created.") from exc
        if self._error:
            raise RuntimeError(self._error)

    def _build_session(
        self,
        voice: str,
        instructions: str,
        turn_detection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        settings = get_settings()
        session: dict[str, Any] = {
            "modalities": ["text", "audio"],
            "voice": voice,
            "instructions": instructions,
        }
        if settings.qwen_family == "audio":
            # Qwen-Audio-Realtime uses a flat session shape.
            session["input_audio_format"] = "pcm"
            session["output_audio_format"] = "pcm"
            session["enable_speech_emotion"] = True
        else:
            session["input_audio_format"] = "pcm"
            session["output_audio_format"] = "pcm"
            session["audio"] = {
                "input": {
                    "format": {
                        "type": "pcm",
                        "sample_rate": 16000,
                        "sample_format": "s16le",
                        "channels": 1,
                        "packing": "interleaved",
                        "channel_layout": "mono",
                    }
                },
                "output": {
                    "voice": voice,
                    "format": {"type": "pcm", "sample_rate": 24000},
                },
            }
        session["turn_detection"] = turn_detection
        return session

    async def configure(
        self,
        voice: str,
        instructions: str,
        turn_detection: dict[str, Any] | None = None,
    ) -> None:
        session = self._build_session(voice, instructions, turn_detection)
        self._session_updated.clear()
        self._error_event.clear()
        self._error = None
        await self.send({"type": "session.update", "session": session})
        wait_updated = asyncio.create_task(self._session_updated.wait())
        wait_error = asyncio.create_task(self._error_event.wait())
        try:
            done, _pending = await asyncio.wait(
                [wait_updated, wait_error], timeout=12, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            wait_updated.cancel()
            wait_error.cancel()
        if wait_error in done:
            raise RuntimeError(self._error or "Qwen rejected the session configuration.")
        if not done:
            log.warning("Qwen did not acknowledge session.update; continuing anyway.")

    async def append_audio(self, pcm16k_b64: str) -> None:
        await self.send({"type": "input_audio_buffer.append", "audio": pcm16k_b64})

    async def request_response(self, instructions: str | None = None) -> None:
        event: dict[str, Any] = {"type": "response.create"}
        if instructions:
            event["response"] = {
                "instructions": instructions,
                "modalities": ["text", "audio"],
            }
            # Qwen-Audio-Realtime rejects response.create unless the conversation
            # already contains a user message, so seed one with the same text.
            if get_settings().qwen_family == "audio":
                await self.send(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": instructions}],
                        },
                    }
                )
        await self.send(event)

    async def send(self, event: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("Qwen session is not connected.")
        await self._ws.send(json.dumps(event))

    async def close(self) -> None:
        self._closed.set()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "session.created":
                    self._session_ready.set()
                elif event_type == "session.updated":
                    self._session_updated.set()
                elif event_type == "error":
                    error_obj = event.get("error") or {}
                    self._error = error_obj.get("message") or json.dumps(event)
                    log.error("Qwen error: %s", self._error)
                    self._session_ready.set()
                    self._error_event.set()
                elif event_type == "response.audio.delta" and self.on_audio:
                    delta = event.get("delta") or event.get("audio")
                    if delta:
                        await self.on_audio(delta)
                if self.on_event:
                    await self.on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc)
            log.exception("Qwen websocket closed with error")
            self._session_ready.set()
        finally:
            self._closed.set()
