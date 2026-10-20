"""Print the exact entry prompt used when a call is answered."""

from app.db import SessionLocal
from app.greeting import receptionist_greeting, receptionist_instructions
from app.models import User
from app.provider import resolve_active_voice, resolve_delivery

db = SessionLocal()
user = db.query(User).filter(User.email == "owner@testdesk.local").first()
voice, why = resolve_active_voice(db, user)

greeting = receptionist_greeting(user.business_name, user.full_name)
instructions = receptionist_instructions(
    user.business_name, user.full_name, greeting, delivery=resolve_delivery(db, user)
)

print("ANSWERING VOICE:", (voice.name if voice else None), "-", (voice.voice_id if voice else "default"))
print("WHY:", why)
print()
print("=== SYSTEM INSTRUCTIONS (session configure) ===")
print(instructions)
print()
print("=== FIRST-UTTERANCE COMMAND (request_response) ===")
print(f'Say exactly this greeting, then wait for the caller: "{greeting}"')
