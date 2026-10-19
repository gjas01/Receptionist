"""Print the exact system instruction the live code produces for this account."""

from app.db import SessionLocal
from app.greeting import receptionist_greeting, receptionist_instructions
from app.models import User
from app.provider import resolve_delivery

db = SessionLocal()
user = db.query(User).first()
greeting = receptionist_greeting(user.business_name, user.full_name)
instructions = receptionist_instructions(
    user.business_name, user.full_name, greeting, delivery=resolve_delivery(user)
)
print("GREETING:", greeting)
print()
print("FULL SYSTEM INSTRUCTION:")
print(instructions)
db.close()
