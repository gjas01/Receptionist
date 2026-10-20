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


asyncio.run(main())
