"""Silent idle timeout for live and test calls.

If nobody on the caller's side has been heard for `idle_seconds` — measured
from the moment the receptionist's last word finishes playing — the call is
closed quietly: no "Can I help?" prompts, no goodbye sentence. Any caller
speech restarts the clock. Set CALL_IDLE_TIMEOUT=0 to disable and leave the
line open forever.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)


class IdleMonitor:
    def __init__(
        self,
        idle_seconds: float,
        on_idle: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        self.idle_seconds = idle_seconds
        self._on_idle = on_idle
        self._last_activity = time.monotonic()
        # Monotonic time at which the receptionist's queued audio finishes
        # playing; the idle clock must not run while she is still talking.
        self._playback_until = 0.0
        self._ended = False
        self._task: asyncio.Task | None = None
        # Set when the monitor wants the call loop to wind down.
        self.ended = asyncio.Event()

    def start(self) -> None:
        if self.idle_seconds <= 0:
            return  # disabled: keep the line open indefinitely
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._ended = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ---- feeds from the call handlers ------------------------------------

    def handle_event(self, event: dict[str, Any]) -> None:
        """Caller speech restarts the clock. Both engines emit at least the
        input-transcription event; Qwen also sends speech_started."""
        if (event.get("type") or "") in (
            "input_audio_buffer.speech_started",
            "conversation.item.input_audio_transcription.completed",
        ):
            self._last_activity = time.monotonic()

    def track_audio(self, delta_b64: str) -> None:
        """Extend the playback-end estimate for audio streamed to the caller.

        Call this from `on_audio` with every outgoing chunk. The model streams
        faster than real time, so keep a virtual play queue: each chunk is
        scheduled after everything already queued. 24 kHz mono s16le = 48000
        bytes per second of speech.
        """
        if not delta_b64:
            return
        try:
            approx_bytes = (len(delta_b64) // 4) * 3
        except TypeError:
            return
        seconds = approx_bytes / 48000.0
        now = time.monotonic()
        self._playback_until = max(now, self._playback_until) + seconds

    # ---- internals ---------------------------------------------------------

    async def _run(self) -> None:
        while not self._ended:
            await asyncio.sleep(1.0)
            silence_start = max(self._last_activity, self._playback_until)
            if time.monotonic() - silence_start < self.idle_seconds:
                continue
            self._ended = True
            log.info(
                "No caller audio for %ss: closing the call quietly", self.idle_seconds
            )
            self.ended.set()
            try:
                await self._on_idle()
            except Exception:
                log.debug("Idle hangup callback failed", exc_info=True)
