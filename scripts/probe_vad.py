"""Probe which server_vad options the Qwen realtime endpoint accepts.

Sends session.update variants and reports whether each is acknowledged or
rejected with an error.
"""

import asyncio
import json

from app.qwen import QwenRealtimeSession

VARIANTS = {
    "current (type+threshold+silence)": {
        "type": "server_vad",
        "threshold": 0.3,
        "silence_duration_ms": 700,
    },
    "higher threshold 0.6": {
        "type": "server_vad",
        "threshold": 0.6,
        "silence_duration_ms": 700,
    },
    "+prefix_padding_ms": {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 800,
    },
    "+create_response false": {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 800,
        "create_response": False,
    },
    "+interrupt_response false": {
        "type": "server_vad",
        "threshold": 0.5,
        "silence_duration_ms": 800,
        "interrupt_response": False,
    },
}


async def try_variant(name: str, vad: dict) -> None:
    session = QwenRealtimeSession()
    try:
        await session.connect()
        try:
            await session.configure(
                voice="Tina", instructions="Probe test.", turn_detection=vad
            )
            print(f"[OK]    {name}")
        except RuntimeError as exc:
            print(f"[REJECT] {name}: {exc}")
    except Exception as exc:
        print(f"[CONNECT-FAIL] {name}: {exc}")
    finally:
        await session.close()


async def main() -> None:
    for name, vad in VARIANTS.items():
        await try_variant(name, vad)
        await asyncio.sleep(0.5)


asyncio.run(main())
