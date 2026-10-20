from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: str = "qwen"  # `qwen` or `gemini` (env: PROVIDER)
    api_endpoint: str = "https://maas.qwencloudapi.com"
    dashscope_key: str = ""
    qwen_model: str = "qwen3.8-omni-flash-realtime"
    # Used when no voice has been cloned yet. Each model family has its own voice list.
    qwen_preset_voice: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-live"
    gemini_preset_voice: str = ""
    # Free-form delivery guidance for the voice (accent, age, pace, tone).
    qwen_voice_delivery: str = (
        "Speak as a warm, unhurried 22 year old British receptionist from Edinburgh; "
        "keep sentences short and calm"
    )
    # Voice-activity detection for incoming caller audio (env: VAD_*).
    # threshold: 0..1 — higher means quieter sounds stop counting as speech.
    # silence_duration_ms: how long the caller must be quiet before their turn
    # ends — longer tolerates mid-sentence pauses without a premature reply.
    # prefix_padding_ms: audio kept from just before the trigger so the first
    # syllable isn't clipped.
    vad_threshold: float = 0.5
    vad_silence_duration_ms: int = 800
    vad_prefix_padding_ms: int = 300
    # Hard floor for turn-end detection: the caller must be silent at least
    # this long before their answer is considered complete and the
    # receptionist may respond — never interrupt mid-sentence (env:
    # VAD_MIN_SILENCE_MS). A smaller vad_silence_duration_ms is raised to this.
    vad_min_silence_ms: int = 500
    # Idle timeout: seconds without hearing the caller before the call is
    # closed quietly (no prompts, no goodbye). 0 = keep the line open forever
    # (env: CALL_IDLE_TIMEOUT). Measured from the end of the receptionist's
    # last word.
    call_idle_timeout: float = 60.0
    secret_key: str = "dev-only-change-me"
    public_base_url: str = "http://localhost:8000"
    data_dir: str = "./data"

    @property
    def api_base(self) -> str:
        endpoint = (self.api_endpoint or "").strip().rstrip("/")
        if endpoint.endswith("/api/v1"):
            endpoint = endpoint[: -len("/api/v1")]
        return endpoint

    @property
    def voice_clone_url(self) -> str:
        return f"{self.api_base}/api/v1/services/audio/tts/customization"

    @property
    def realtime_ws_url(self) -> str:
        parsed = urlparse(self.api_base)
        scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
        netloc = parsed.netloc or parsed.path
        ws_base = urlunparse((scheme, netloc, "", "", "", ""))
        return f"{ws_base}/api-ws/v1/realtime?model={self.qwen_model}"

    @property
    def public_ws_base(self) -> str:
        parsed = urlparse(self.public_base_url.rstrip("/"))
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, "", "", "", ""))

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_path / 'receptionist.db'}"

    @property
    def qwen_configured(self) -> bool:
        return bool(self.dashscope_key.strip() and self.api_endpoint.strip())

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key.strip())

    @staticmethod
    def normalize_provider(provider: str | None, default: str = "qwen") -> str:
        p = (provider or "").strip().lower()
        return p if p in ("qwen", "gemini") else default

    def model_for(self, provider: str) -> str:
        return self.gemini_model if provider == "gemini" else self.qwen_model

    def configured_for(self, provider: str) -> bool:
        return self.gemini_configured if provider == "gemini" else self.qwen_configured

    @property
    def active_provider(self) -> str:
        return self.normalize_provider(self.provider, "qwen")

    @property
    def active_model(self) -> str:
        return self.model_for(self.active_provider)

    @property
    def active_configured(self) -> bool:
        return self.configured_for(self.active_provider)

    @property
    def gemini_ws_url(self) -> str:
        return (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            f"?key={self.gemini_api_key.strip()}"
        )

    @property
    def qwen_family(self) -> str:
        """`audio` for the Qwen-Audio-Realtime family, otherwise `omni`."""
        return "audio" if "audio-" in self.qwen_model else "omni"

    def default_voice_for(self, provider: str) -> str:
        if provider == "gemini":
            return self.gemini_preset_voice.strip() or "Sulafat"
        if self.qwen_preset_voice.strip():
            return self.qwen_preset_voice.strip()
        if self.qwen_family == "audio":
            return "loongmary"
        return "Tina"

    @property
    def default_voice(self) -> str:
        return self.default_voice_for(self.active_provider)


@lru_cache
def get_settings() -> Settings:
    return Settings()
