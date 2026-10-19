"""Verify the per-voice shaping prompt: migration, resolve_delivery, live preview."""

import asyncio
import base64

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.greeting import receptionist_greeting, receptionist_instructions
from app.models import User, VoiceClone
from app.provider import resolve_delivery, resolve_voice
from app.gemini import GeminiRealtimeSession

settings = get_settings()
init_db()  # should add the delivery column idempotently
init_db()  # twice = must not error

db = SessionLocal()
user = db.query(User).first()
assert user, "no user in DB"

clone = user.voice or VoiceClone(user_id=user.id)
clone.voice_id = "Sulafat"
clone.target_model = settings.gemini_model
clone.preferred_name = "preset"
clone.delivery = "Speak like a cheerful school crossing lollipop woman from Newcastle"
if user.voice is None:
    db.add(clone)
db.commit()

assert resolve_delivery(user) == clone.delivery
assert resolve_voice(user) == "Sulafat"
print("resolve_delivery / resolve_voice from DB: OK")

instructions = receptionist_instructions("Acme", "Jordan", receptionist_greeting("Acme", "Jordan"), delivery=resolve_delivery(user))
assert "Newcastle" in instructions
print("instructions carry saved shaping prompt: OK")

pcm = bytearray()
transcript: list[str] = []
done = asyncio.Event()


async def on_audio(delta: str) -> None:
    pcm.extend(base64.b64decode(delta))


async def on_event(event: dict) -> None:
    t = event.get("type")
    if t == "response.audio_transcript.done":
        transcript.append(event.get("transcript", ""))
    elif t == "response.done":
        done.set()
    elif t == "error":
        print("ERROR:", str(event)[:400])
        done.set()


async def main() -> None:
    session = GeminiRealtimeSession(on_audio=on_audio, on_event=on_event)
    await session.connect()
    await session.configure(voice=resolve_voice(user), instructions=instructions, turn_detection=None)
    greeting = receptionist_greeting("Acme", "Jordan")
    await session.request_response(f'Say exactly this greeting: "{greeting}"')
    await asyncio.wait_for(done.wait(), timeout=60)
    await session.close()
    print("audio_bytes:", len(pcm), "| transcript:", " ".join(transcript)[:150])
    assert len(pcm) > 20000
    print("SHAPING CHECK PASSED")


asyncio.run(main())
db.close()
