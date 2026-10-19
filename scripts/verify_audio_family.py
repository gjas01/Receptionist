"""Verify the audio-family realtime path and template rendering inside the container."""

import asyncio
import base64
import json
from pathlib import Path

from app.config import get_settings
from app.greeting import receptionist_greeting, receptionist_instructions
from app.qwen import QwenRealtimeSession, resolve_voice
from app.routes.realtime import VAD

settings = get_settings()
print("model:", settings.qwen_model, "| family:", settings.qwen_family, "| voice:", settings.default_voice)
print("ws url:", settings.realtime_ws_url)


async def realtime_check() -> None:
    pcm = bytearray()
    transcript: list[str] = []
    done = asyncio.Event()

    async def on_audio(delta: str) -> None:
        pcm.extend(base64.b64decode(delta))

    async def on_event(event: dict) -> None:
        if event.get("type") == "response.audio_transcript.done":
            transcript.append(event.get("transcript", ""))
        elif event.get("type") == "response.done":
            done.set()
        elif event.get("type") == "error":
            print("ERROR EVENT:", json.dumps(event)[:400])
            done.set()

    session = QwenRealtimeSession(on_audio=on_audio, on_event=on_event)
    await session.connect()
    greeting = receptionist_greeting("Acme Ltd", "Jordan")
    instructions = receptionist_instructions("Acme Ltd", "Jordan", greeting)
    # Exercise the same configure() path the app uses, with VAD as in a live call.
    await session.configure(voice=resolve_voice(None), instructions=instructions, turn_detection=VAD)
    print("session.update acknowledged (audio-family flat shape OK)")
    await session.request_response(f'Say exactly this greeting: "{greeting}"')
    await asyncio.wait_for(done.wait(), timeout=60)
    await session.close()
    print("audio_bytes:", len(pcm), "| transcript:", " ".join(transcript)[:200])
    assert len(pcm) > 20000, "too little audio returned"


def template_check() -> None:
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="app/templates")
    ctx = {
        "request": None,
        "user": None,
        "flash_messages": [],
        "test_mode": True,
        "qwen_configured": True,
        "qwen_model": settings.qwen_model,
        "qwen_family": settings.qwen_family,
        "qwen_voice": settings.default_voice,
        "voice_status": "default",
        "greeting_preview": "Good afternoon, this is Acme Ltd, how can I help?",
        "time_of_day": "afternoon",
    }

    class FakeUser:
        email = "a@b.co"
        business_name = "Acme"
        full_name = "Jordan"
        voice = None

        class phone:
            twilio_phone_number = "+441234567890"
            webhook_registered = True

    ctx["user"] = FakeUser()
    for name in ("dashboard.html", "voice.html"):
        html = templates.env.get_template(name).render(**ctx)
        assert "Simulate incoming call" in html if name == "dashboard.html" else True
        print(name, "renders OK,", len(html), "chars")


asyncio.run(realtime_check())
template_check()
Path("/tmp/verify.txt").write_text("done")
print("ALL CHECKS PASSED")