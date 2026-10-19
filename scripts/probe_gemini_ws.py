"""Iterate on the raw WebSocket setup shape until Gemini accepts it."""

import asyncio
import json

import websockets

from app.config import get_settings

settings = get_settings()
url = settings.gemini_ws_url

SETUP = {
    "setup": {
        "model": f"models/{settings.gemini_model}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Sulafat"}}
            },
        },
        "systemInstruction": {"parts": [{"text": "You are a helpful assistant."}]},
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "realtimeInputConfig": {
            "automaticActivityDetection": {
                "startOfSpeechSensitivity": "START_SENSITIVITY_HIGH",
                "silenceDurationMs": 700,
            }
        },
    }
}


async def main() -> None:
    ws = await websockets.connect(url, max_size=16 * 1024 * 1024)
    await ws.send(json.dumps(SETUP))
    try:
        for i in range(6):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            print(f"frame {i}: {text[:500]}")
            if '"setupComplete"' in text:
                print("SETUP ACCEPTED")
                # Now send a text kick and see if audio comes back.
                kick = {
                    "clientContent": {
                        "turns": [{"role": "user", "parts": [{"text": "Say: Good evening, this is Acme Ltd, how can I help?"}]}],
                        "turnComplete": True,
                    }
                }
                await ws.send(json.dumps(kick))
                audio_frames = 0
                for j in range(40):
                    raw = await asyncio.wait_for(ws.recv(), timeout=20)
                    text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                    if '"inlineData"' in text:
                        audio_frames += 1
                    elif '"turnComplete"' in text or "Transcription" in text:
                        print(f"resp {j}: {text[:300]}")
                    if '"turnComplete": true' in text.replace(" ", "") or '"turnComplete":true' in text.replace(" ", ""):
                        print(f"turn complete after {j} messages, audio_frames={audio_frames}")
                        break
                else:
                    print(f"no turnComplete; audio_frames={audio_frames}")
                break
    except TimeoutError:
        print("TIMEOUT waiting for frames")
    except websockets.exceptions.ConnectionClosedError as exc:
        print("CLOSED:", exc.code, exc.reason[:400])
    finally:
        try:
            await ws.close()
        except Exception:
            pass


asyncio.run(main())