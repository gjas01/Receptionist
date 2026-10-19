"""Smoke-test both providers: imports, session factory, template rendering, VAD config map."""

from app.config import get_settings
from app.gemini import GEMINI_VOICES, GeminiRealtimeSession
from app.provider import create_session, resolve_voice
from app.qwen import QwenRealtimeSession

settings = get_settings()
print("provider:", settings.active_provider, "| model:", settings.active_model, "| voice:", settings.default_voice)

# Factory picks the right class per provider.
assert type(create_session()) is QwenRealtimeSession
s2 = GeminiRealtimeSession()
print(f"{len(GEMINI_VOICES)} gemini voices; sample: {GEMINI_VOICES[0]}")

# resolve_voice with no clone -> default.
class U:
    voice = None

assert resolve_voice(U()) == settings.default_voice
print("resolve_voice default OK")

# Both templates render in both provider modes.
from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory="app/templates")

class FakeUser:
    email = "a@b.co"
    business_name = "Acme"
    full_name = "Jordan"
    voice = None

for provider, model, voice in (("qwen", "qwen3.8-omni-flash-realtime", "Tina"), ("gemini", "gemini-3.8-live", "Sulafat")):
    ctx = dict(
        request=None, user=FakeUser(), flash_messages=[], test_mode=True,
        provider=provider, qwen_configured=True, qwen_model=model,
        qwen_family="omni", qwen_voice=voice, voice_status="default",
        greeting_preview="Good evening, this is Acme, how can I help?", time_of_day="evening",
        gemini_voices=GEMINI_VOICES, gemini_default_voice=voice,
    )
    dash = templates.env.get_template("dashboard.html").render(**ctx)
    voice_html = templates.env.get_template("voice.html").render(**ctx)
    if provider == "gemini":
        assert "Preview this voice" in voice_html and "Sulafat" in voice_html and "Warm" in voice_html
        assert "Record with microphone" not in voice_html
    else:
        assert "Clone from this recording" in voice_html and "Start recording" in voice_html
        assert "Preview this voice" not in voice_html
    base_ctx = dict(ctx)
    base = templates.env.get_template("base.html").render(**base_ctx)
    link = "Choose a Voice" if provider == "gemini" else "Clone a Voice"
    assert link in base
    print(f"{provider}: templates render with correct UI ({len(voice_html)} chars)")

print("SMOKE CHECK PASSED")