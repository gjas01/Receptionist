"""Unit-style test of the silence guard with a fake Qwen session."""

import asyncio

import app.call_guard
from app.call_guard import CallGuard

app.call_guard.FAREWELL_PLAY_SECONDS = 0.2  # speed up test


class FakeQwen:
    def __init__(self):
        self.requests = []

    async def request_response(self, instructions=None):
        self.requests.append(instructions or "")

    async def send_event(self, event):
        pass


async def main():
    qwen = FakeQwen()
    hung = asyncio.Event()

    async def hangup():
        hung.set()

    guard = CallGuard(qwen, idle_seconds=0.4, hangup=hangup)
    guard.start()

    # Simulate the greeting response finishing, then total silence.
    guard.handle_event({"type": "response.created"})
    guard.handle_event({"type": "response.done"})

    # Wait for ask1, ask2, farewell.
    try:
        await asyncio.wait_for(hung.wait(), timeout=6)
    except TimeoutError:
        print("FAIL: hangup never fired; requests:", qwen.requests)
        await guard.stop()
        return

    asks = [r for r in qwen.requests if "Can I help" in r]
    farewells = [r for r in qwen.requests if "call back" in r]
    print("asks:", len(asks), "| farewell instructions:", len(farewells))
    assert len(asks) == 2, "expected two 'Can I help?' asks"
    assert len(farewells) >= 1, "expected farewell instruction before hangup"
    assert guard.ended.is_set(), "ended event should be set"
    await guard.stop()
    print("GUARD SEQUENCE OK")

    # Caller speech must reset the counters.
    qwen2 = FakeQwen()
    hung2 = asyncio.Event()

    async def hangup2():
        hung2.set()

    guard2 = CallGuard(qwen2, idle_seconds=0.4, hangup=hangup2)
    guard2.start()
    guard2.handle_event({"type": "response.done"})
    await asyncio.sleep(0.6)  # one ask happens
    guard2.handle_event({"type": "input_audio_buffer.speech_started"})
    guard2.handle_event({"type": "conversation.item.input_audio_transcription.completed"})
    await asyncio.sleep(1.2)
    assert not hung2.is_set(), "call should not end once the caller speaks"
    await guard2.stop()
    print("CALLER SPEECH RESET OK")

    # The idle clock must start when her audio finishes, not when generation
    # ends. Feed a ~2s synthetic response, then time the first ask.
    qwen3 = FakeQwen()
    hung3 = asyncio.Event()

    async def hangup3():
        hung3.set()

    guard3 = CallGuard(qwen3, idle_seconds=1.0, hangup=hangup3)
    guard3.start()
    guard3.handle_event({"type": "response.created"})
    # 2 seconds of 24kHz s16 mono = 96000 bytes -> base64 ~128000 chars.
    import base64

    chunk = base64.b64encode(b"\0" * 48000).decode()  # 1 second of audio
    guard3.handle_event({"type": "response.audio.delta", "delta": chunk})
    guard3.handle_event({"type": "response.audio.delta", "delta": chunk})
    t_done = asyncio.get_event_loop().time()
    guard3.handle_event({"type": "response.done"})
    await asyncio.sleep(0.9)  # 0.9s after generation ended, audio still "playing"
    early_asks = len([r for r in qwen3.requests if "Can I help" in r])
    await asyncio.sleep(2.5)  # audio (~2s) + idle window (1s) have now passed
    late_asks = len([r for r in qwen3.requests if "Can I help" in r])
    print(f"asks 0.9s after done: {early_asks} | later: {late_asks}")
    assert early_asks == 0, "asked before the receptionist finished speaking"
    assert late_asks >= 1, "never asked even after audio + idle window passed"
    await guard3.stop()
    print("PLAYBACK-ANCHORED PAUSE OK")


asyncio.run(main())
