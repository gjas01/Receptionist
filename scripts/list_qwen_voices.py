"""List server-side Qwen cloned voices (they persist even though DB records were overwritten)."""

import asyncio
import json

import httpx

from app.config import get_settings

settings = get_settings()
headers = {"Authorization": f"Bearer {settings.dashscope_key}", "Content-Type": "application/json"}


async def post(payload: dict) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(settings.voice_clone_url, headers=headers, json=payload)
        print(response.status_code, response.text[:1500])


async def main() -> None:
    for action in ("list", "list_voice"):
        print(f"--- qwen-voice-enrollment {action}:")
        await post({"model": "qwen-voice-enrollment", "input": {"action": action}})
    print("--- voice-enrollment list_voice prefix qwen-omni:")
    await post(
        {
            "model": "voice-enrollment",
            "input": {"action": "list_voice", "prefix": "ord"},
        }
    )


asyncio.run(main())