"""Silence guard for live and test calls.

The realtime model only speaks when a turn is triggered, so total silence
would otherwise last forever. This guard watches each call: after
`idle_seconds` without hearing the caller it asks "Can I help?", once more if
they still haven't answered, then says a scripted goodbye and ends the call.
Any caller speech resets the count. The matching prompt rules in
app/greeting.py let the model itself refuse to react to background chatter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)

FOLLOWUP_INSTRUCTIONS = (
    "There has been a pause and you have not heard the caller. Say exactly, "
    "warmly and briefly: \"Can I help?\""
)
FAREWELL_TEXT = "Sorry, I can't hear you. Please call back."
MAX_ASKS = 2
# Grace period for the goodbye sentence to finish playing before hanging up.
FAREWELL_PLAY_SECONDS = 7


def is_farewell(text: str | None) -> bool:
    """True if the agent just said the can't-hear-you goodbye line."""
    if not text:
        return False
    t = text.lower().replace("\u2019", "'")
    return "can't hear you" in t and "call back" in t


class CallGuard:
    def __init__(
        self,
        qwen: Any,
        *,
        idle_seconds: float,
        hangup: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        self.qwen = qwen
        self.idle_seconds = idle_seconds
        self._hangup = hangup
        self._agent_speaking = False
        self._last_activity = time.monotonic()
        self._asks = 0
        self._ended = False
        self._closing = False
        self._task: asyncio.Task | None = None
        # Set when the guard wants the call to end (after the goodbye).
        self.ended = asyncio.Event()

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
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

    # ---- event feed (from the Qwen session) -----------------------------

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type") or ""
        if event_type == "response.created":
            self._agent_speaking = True
        elif event_type == "response.done":
            self._agent_speaking = False
            self._last_activity = time.monotonic()
        elif event_type in (
            "input_audio_buffer.speech_started",
            "conversation.item.input_audio_transcription.completed",
        ):
            # Somebody made a plausible voice: stop counting silence.
            self._asks = 0
            self._last_activity = time.monotonic()
        elif event_type == "response.audio_transcript.done":
            # The model itself delivered the goodbye (per prompt rules): end call.
            if is_farewell(event.get("transcript")):
                self._begin_close()

    # ---- internals -------------------------------------------------------

    async def _run(self) -> None:
        tick = max(0.3, self.idle_seconds / 4)
        while not self._ended:
            await asyncio.sleep(tick)
            if self._ended or self._agent_speaking:
                continue
            if time.monotonic() - self._last_activity < self.idle_seconds:
                continue
            self._asks += 1
            self._last_activity = time.monotonic()
            if self._asks <= MAX_ASKS:
                log.info("Call idle: asking \"Can I help?\" (%d/%d)", self._asks, MAX_ASKS)
                await self.qwen.request_response(instructions=FOLLOWUP_INSTRUCTIONS)
            else:
                log.info("Call idle after %d asks: ending the call", MAX_ASKS)
                self._ended = True
                await self.qwen.request_response(
                    instructions=(
                        "You still cannot hear the caller. Say exactly this and then "
                        f"stop: \"{FAREWELL_TEXT}\""
                    )
                )
                self._begin_close()
                return

    def _begin_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._ended = True

        async def _close() -> None:
            await asyncio.sleep(FAREWELL_PLAY_SECONDS)
            self.ended.set()  # let the call loop wind down
            try:
                await self._hangup()
            except Exception:
                log.debug("Guard hangup: socket already closed", exc_info=True)

        asyncio.create_task(_close())
