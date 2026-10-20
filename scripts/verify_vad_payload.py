"""Configure a session with the app's real _vad() settings; report OK or error."""

import asyncio

from app.qwen import QwenRealtimeSession
from app.routes.realtime import _vad


async def main() -> None:
    vad = _vad()
    print("payload:", vad)
    session = QwenRealtimeSession()
    try:
        await session.connect()
        try:
            await session.configure(voice="Tina", instructions="Probe test.", turn_detection=vad)
            print("[OK] final VAD payload accepted")
        except RuntimeError as exc:
            print(f"[REJECT] {exc}")
    finally:
        await session.close()


asyncio.run(main())
