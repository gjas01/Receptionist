from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from collections.abc import Awaitable, Callable

import websockets

from app.config import get_settings

log = logging.getLogger(__name__)

AudioHandler = Callable[[str], Awaitable[None]]
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

# 30 prebuilt voices for Gemini Live (see ai.google.dev/gemini-api/docs/live-api/capabilities).
GEMINI_VOICES: list[dict[str, str]] = [
    {"name": "Zephyr", "trait": "Bright"},
    {"name": "Puck", "trait": "Upbeat"},
    {"name": "Charon", "trait": "Informative"},
    {"name": "Kore", "trait": "Firm"},
    {"name": "Fenrir", "trait": "Excitable"},
    {"name": "Leda", "trait": "Youthful"},
    {"name": "Orus", "trait": "Firm"},
    {"name": "Aoede", "trait": "Breezy"},
    {"name": "Callirrhoe", "trait": "Easy-going"},
    {"name": "Autonoe", "trait": "Bright"},
    {"name": "Enceladus", "trait": "Breathy"},
    {"name": "Iapetus", "trait": "Clear"},
    {"name": "Umbriel", "trait": "Easy-going"},
    {"name": "Algieba", "trait": "Smooth"},
    {"name": "Despina", "trait": "Smooth"},
    {"name": "Erinome", "trait": "Clear"},
    {"name": "Algenib", "trait": "Gravelly"},
    {"name": "Rasalgethi", "trait": "Informative"},
    {"name": "Laomedeia", "trait": "Upbeat"},
    {"name": "Achernar", "trait": "Soft"},
    {"name": "Alnilam", "trait": "Firm"},
    {"name": "Schedar", "trait": "Even"},
    {"name": "Gacrux", "trait": "Mature"},
    {"name": "Pulcherrima", "trait": "Forward"},
    {"name": "Achird", "trait": "Friendly"},
    {"name": "Zubenelgenubi", "trait": "Casual"},
    {"name": "Vindemiatrix", "trait": "Gentle"},
    {"name": "Sadachbia", "trait": "Lively"},
    {"name": "Sadaltager", "trait": "Knowledgeable"},
    {"name": "Sulafat", "trait": "Warm"},
]

DEFAULT_GEMINI_VOICE = "Sulafat"


class GeminiRealtimeSession:
    """WebSocket client for Gemini Live (BidiGenerateContent), interface-compatible
    with QwenRealtimeSession so route handlers stay provider-agnostic."""

    def __init__(
        self,
        on_audio: AudioHandler | None = None,
        on_event: EventHandler | None = None,
    ) -> None:
        self.on_audio = on_audio
        self.on_event = on_event
        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._setup_ready = asyncio.Event()
        self._closed = asyncio.Event()
        self._error: str | None = None
        self._in_buffer: str = ""
        self._out_buffer: str = ""

    async def connect(self) -> None:
        settings = get_settings()
        if not settings.gemini_configured:
            raise RuntimeError("GEMINI_API_KEY must be set in the .env file.")
        url = settings.gemini_ws_url
        log.info("Connecting to Gemini Live (model=%s)", settings.gemini_model)
        self._ws = await websockets.connect(url, max_size=16 * 1024 * 1024)
        self._reader_task = asyncio.create_task(self._read_loop())
        # Gemini sends no event until we send `setup` (in configure()).

    async def configure(
        self,
        voice: str,
        instructions: str,
        turn_detection: dict[str, Any] | None = None,
    ) -> None:
        """Send the setup message. `turn_detection` mirrors the Qwen interface:
        None means no VAD (push-to-talk style), a dict enables automatic VAD."""
        settings = get_settings()
        # Raw BidiGenerateContentSetup (proto JSON): generation config nests under
        # `generationConfig`; VAD has no numeric threshold field.
        setup: dict[str, Any] = {
            "model": f"models/{settings.gemini_model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                },
            },
            "systemInstruction": {"parts": [{"text": instructions}]},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }
        if turn_detection is not None:
            vad = turn_detection or {}
            setup["realtimeInputConfig"] = {
                "automaticActivityDetection": {
                    "startOfSpeechSensitivity": "START_SENSITIVITY_HIGH",
                    "silenceDurationMs": int(vad.get("silence_duration_ms", 700)),
                }
            }
        self._setup_ready.clear()
        self._error = None
        await self.send({"setup": setup})
        try:
            await asyncio.wait_for(self._setup_ready.wait(), timeout=20)
        except TimeoutError as exc:
            raise RuntimeError("Timed out waiting for Gemini setupComplete.") from exc
        if self._error:
            raise RuntimeError(self._error)

    async def append_audio(self, pcm16k_b64: str) -> None:
        await self.send(
            {
                "realtimeInput": {
                    "audio": {"mimeType": "audio/pcm;rate=16000", "data": pcm16k_b64}
                }
            }
        )

    async def request_response(self, instructions: str | None = None) -> None:
        """Kick the model with a user text turn (e.g. the greeting script)."""
        text = instructions or "Begin the call with the greeting now."
        await self.send(
            {
                "clientContent": {
                    "turns": [{"role": "user", "parts": [{"text": text}]}],
                    "turnComplete": True,
                }
            }
        )

    async def cancel_response(self) -> None:
        await self.send({"clientContent": {"turns": [], "turnComplete": True}})

    async def send(self, message: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("Gemini session is not connected.")
        await self._ws.send(json.dumps(message))

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
                    msg = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                await self._handle(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc)
            log.exception("Gemini websocket closed with error")
            self._setup_ready.set()
        finally:
            self._closed.set()

    async def _handle(self, msg: dict[str, Any]) -> None:
        if "setupComplete" in msg:
            self._setup_ready.set()
            await self._emit({"type": "session.created"})
            return

        if "goAway" in msg:
            log.info("Gemini goAway received; closing soon.")
            return

        if "error" in msg:
            err = msg.get("error") or {}
            self._error = err.get("message") or json.dumps(msg)
            log.error("Gemini error: %s", self._error)
            self._setup_ready.set()
            await self._emit({"type": "error", "error": {"message": self._error}})
            return

        server = msg.get("serverContent") or {}
        model_turn = server.get("modelTurn") or {}
        parts = model_turn.get("parts") or []

        # When the model starts answering, the caller's turn is over: emit the
        # buffered input transcript once, mirroring the Qwen event we forward.
        flush_on_parts = parts and self._in_buffer
        if flush_on_parts:
            await self._flush_input_transcript()

        out_parts: list[str] = []
        for part in parts:
            inline = part.get("inlineData") or {}
            data = inline.get("data")
            if data:
                if self.on_audio:
                    await self.on_audio(data)
            elif part.get("text"):
                out_parts.append(part["text"])

        out_tr = server.get("outputTranscription") or {}
        if out_tr.get("text"):
            self._out_buffer += out_tr["text"]

        in_tr = server.get("inputTranscription") or {}
        if in_tr.get("text"):
            self._in_buffer += in_tr["text"]

        if server.get("turnComplete"):
            await self._flush_input_transcript()
            transcript = self._out_buffer.strip() or " ".join(out_parts).strip()
            self._out_buffer = ""
            if transcript:
                await self._emit(
                    {"type": "response.audio_transcript.done", "transcript": transcript}
                )
            await self._emit({"type": "response.done"})

    async def _flush_input_transcript(self) -> None:
        text = self._in_buffer.strip()
        self._in_buffer = ""
        if text:
            await self._emit(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": text,
                }
            )

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event:
            try:
                await self.on_event(event)
            except Exception:  # noqa: BLE001
                log.exception("Gemini event handler failed")
