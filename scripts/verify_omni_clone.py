"""End-to-end check of the omni clone pipeline and the new voice page."""

import asyncio
import base64
import wave
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.greeting import receptionist_greeting
from app.qwen import QwenRealtimeSession, create_cloned_voice, preferred_voice_name, resolve_voice

settings = get_settings()
assert settings.qwen_family == "omni", settings.qwen_family
print("model:", settings.qwen_model, "| family:", settings.qwen_family, "| default voice:", settings.default_voice)

SCRIPT = (
    "Hello, thank you for calling. I am delighted to help you today. "
    "Please confirm your name and number, and whether you prefer Tuesday at three or Thursday at nine. "
    "I will take a message and make sure someone returns your call promptly. "
    "Thank you very much, and have a pleasant day."
)


async def synthesize_source(path: Path) -> None:
    pcm = bytearray()
    done = asyncio.Event()

    async def on_audio(delta: str) -> None:
        pcm.extend(base64.b64decode(delta))

    async def on_event(event: dict) -> None:
        if event.get("type") in ("response.done", "error"):
            done.set()

    session = QwenRealtimeSession(on_audio=on_audio, on_event=on_event)
    await session.connect()
    await session.configure(
        voice=settings.default_voice,
        instructions="Read the supplied script naturally, then stop.",
        turn_detection=None,
    )
    await session.request_response(instructions=f"Say this script naturally: {SCRIPT}")
    await asyncio.wait_for(done.wait(), timeout=60)
    await session.close()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(bytes(pcm))
    print("source wav:", path.stat().st_size, "bytes")


async def clone_and_preview(wav_path: Path) -> None:
    voice_id = await create_cloned_voice(wav_path.read_bytes(), "audio/wav", preferred_voice_name("Jordan"))
    print("cloned voice:", voice_id)

    class FakeClone:
        pass

    clone = FakeClone()
    clone.voice_id = voice_id
    clone.target_model = settings.qwen_model

    class FakeUser:
        pass

    user = FakeUser()
    user.voice = clone
    picked = resolve_voice(user)
    assert picked == voice_id, picked
    print("resolve_voice picks the clone: OK")

    pcm = bytearray()
    transcript: list[str] = []
    done = asyncio.Event()

    async def on_audio(delta: str) -> None:
        pcm.extend(base64.b64decode(delta))

    async def on_event(event: dict) -> None:
        if event.get("type") == "response.audio_transcript.done":
            transcript.append(event.get("transcript", ""))
        elif event.get("type") in ("response.done", "error"):
            done.set()

    phrase = "Hi Jordan, This is your cloned voice. This is how your phone will be answered in future."
    session = QwenRealtimeSession(on_audio=on_audio, on_event=on_event)
    await session.connect()
    await session.configure(
        voice=resolve_voice(user),
        instructions="You are previewing a cloned voice. Speak the requested sentence naturally, then stop.",
        turn_detection=None,
    )
    await session.request_response(f'Say exactly this sentence and nothing else: "{phrase}"')
    await asyncio.wait_for(done.wait(), timeout=60)
    await session.close()
    print("preview audio_bytes:", len(pcm), "| transcript:", " ".join(transcript)[:200])
    assert len(pcm) > 20000, "too little audio returned"


def template_check() -> None:
    templates = Jinja2Templates(directory="app/templates")

    class FakeUser:
        email = "a@b.co"
        business_name = "Acme"
        full_name = "Jordan"
        voice = None

    html = templates.env.get_template("voice.html").render(
        request=None,
        user=FakeUser(),
        flash_messages=[],
        test_mode=False,
        qwen_configured=True,
        qwen_model=settings.qwen_model,
        qwen_family=settings.qwen_family,
        qwen_voice=settings.default_voice,
        voice_status="default",
    )
    assert "tab-upload" in html and "upload-input" in html and "panel-record" in html
    print("voice.html renders with both tabs: OK")


async def main() -> None:
    template_check()
    wav = Path("/tmp/e2e-source.wav")
    await synthesize_source(wav)
    await clone_and_preview(wav)
    print("E2E CLONE CHECK PASSED")


asyncio.run(main())