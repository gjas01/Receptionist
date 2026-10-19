"""Convert between Twilio mulaw/8kHz and Qwen PCM16 16kHz/24kHz."""

from __future__ import annotations

import audioop
from dataclasses import dataclass, field


@dataclass
class RateState:
    state: object | None = field(default=None)


def mulaw8k_to_pcm16k(payload: bytes, state: RateState) -> bytes:
    pcm8k = audioop.ulaw2lin(payload, 2)
    pcm16k, state.state = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, state.state)
    return pcm16k


def pcm24k_to_mulaw8k(payload: bytes, state: RateState) -> bytes:
    pcm8k, state.state = audioop.ratecv(payload, 2, 1, 24000, 8000, state.state)
    if not pcm8k:
        return b""
    return audioop.lin2ulaw(pcm8k, 2)
