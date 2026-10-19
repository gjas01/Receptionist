"""Live check: Qwen provider, cloned voice, new receptionist system instruction."""

import asyncio
import base64

from app.config import get_settings
from app.db import SessionLocal
from app.greeting import receptionist_greeting, receptionist_instructions
from app.models import User
from app.provider import create_session, resolve_delivery, resolve_voice

settings = get_settings()
assert settings.active_provider == "qwen", settings.active_provider

db = SessionLocal()
user = db.query(User).filter(User.email == "owner@testdesk.local").first()
voice = resolve_voice(user)
delivery = resolve_delivery(user)
greeting = receptionist_greeting(user.business_name, user.full_name)
instructions = receptionist_instructions(user.business_name, user.full_name, greeting, delivery=delivery)
print("voice:", voice)
assert voice.startswith("qwen-omni-vc-"), "not a cloned voice!"
print("greeting:", greeting)

pcm = bytearray()
transcript: list[str] = []
done = asyncio.Event()


async def on_audio(delta: str) -> None:
    pcm.extend(base64.b64decode(delta))


async def on_event(event: dict) -> None:
    t = event.get("type")
    if t == "response.audio_transcript.done":
        transcript.append(event.get("transcript", ""))
    elif t in ("response.done", "error"):
        if t == "error":
            print("ERROR:", str(event)[:400])
        done.set()


async def main() -> None:
    session = create_session(on_audio=on_audio, on_event=on_event)
    await session.connect()
    await session.configure(voice=voice, instructions=instructions, turn_detection=None)
    await session.request_response(f'Say exactly this greeting: "{greeting}"')
    await asyncio.wait_for(done.wait(), timeout=60)
    await session.close()
    print("audio_bytes:", len(pcm), "| transcript:", " ".join(transcript)[:200])
    assert len(pcm) > 20000, "too little audio"
    print("QWEN CLONED VOICE + NEW PROMPT PASSED")


asyncio.run(main())
db.close()