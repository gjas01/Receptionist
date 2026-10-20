"""Confirm the live prompt contains the noise rules and the app imports."""

from app.greeting import receptionist_greeting, receptionist_instructions
from app.routes import realtime  # noqa: F401  (fails if wiring is broken)

g = receptionist_greeting("Castle Street Studio", "Jordan Hale")
text = receptionist_instructions("Castle Street Studio", "Jordan Hale", g, delivery="Speak warmly.")
for token in ["BACKGROUND NOISE", "keep the line open", "RESPONSE MANAGEMENT"]:
    assert token in text, f"missing: {token}"
for token in ["Please call back", "the system will end the call"]:
    assert token not in text, f"silence-hangup rule still present: {token}"
print("PROMPT OK — noise + response-management rules present, silence hangup removed")
