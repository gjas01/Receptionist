"""Live test: greeting through Gemini Live with the receptionist persona."""

import asyncio
import base64
import wave
from pathlib import Path

from app.config import get_settings
from app.gemini import GeminiRealtimeSession
from app.greeting import receptionist_greeting, receptionist_instructions
from app.routes.realtime import VAD

settings = get_settings()
print("provider:", settings.active_provider, "| model:", settings.gemini_model, "| voice:", settings.default_voice)

voice = settings.default_voice  # Sulafat unless overridden
done = asyncio.Event()
error = asyncio.Event()
pcm = bytearray()
transcript: list[str] = []


async def on_audio(delta_b64: str) -> None:
    pcm.extend(base64.b64decode(delta_b64))


async def on_event(event: dict) -> None:
    t = event.get("type")
    if t == "response.audio_transcript.done":
        transcript.append(event.get("transcript", ""))
    elif t == "response.done":
        done.set()
    elif t == "error":
        print("ERROR:", str(event)[:500])
        error.set()


async def main() -> None:
    session = GeminiRealtimeSession(on_audio=on_audio, on_event=on_event)
    await session.connect()
    print("connected, setupComplete received")
    greeting = receptionist_greeting("Acme Ltd", "Jordan")
    instructions = receptionist_instructions("Acme Ltd", "Jordan", greeting)
    print("delivery in instructions:", "Edinburgh" in instructions)
    await session.configure(voice=voice, instructions=instructions, turn_detection=VAD)
    print("session configured with voice:", voice)
    await session.request_response(f'Say exactly this greeting: "{greeting}"')

    finished, pending = await asyncio.wait(
        [asyncio.create_task(done.wait()), asyncio.create_task(error.wait())],
        timeout=60,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    await session.close()

    print("audio_bytes:", len(pcm), "| transcript:", " ".join(transcript)[:200])
    if pcm:
        out = Path("/tmp/gemini-greeting.wav")
        with wave.open(str(out), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(24000)
            f.writeframes(bytes(pcm))
        print("saved:", out)
    assert len(pcm) > 20000, "too little audio"
    assert not error.is_set(), "error event received"
    print("GEMINI LIVE GREETING PASSED")


asyncio.run(main())