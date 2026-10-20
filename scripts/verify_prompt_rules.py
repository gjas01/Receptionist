"""Confirm the live prompt contains the new noise/silence rules and the app imports."""

from app.greeting import receptionist_greeting, receptionist_instructions
from app.routes import realtime  # noqa: F401  (fails if wiring is broken)
from app.twilio_util import hangup_call  # noqa: F401

g = receptionist_greeting("Castle Street Studio", "Jordan Hale")
text = receptionist_instructions("Castle Street Studio", "Jordan Hale", g, delivery="Speak warmly.")
for token in ["BACKGROUND NOISE", "Can I help?", "Sorry, I can't hear you. Please call back."]:
    assert token in text, f"missing: {token}"
print("PROMPT OK — all silence/noise rules present")
