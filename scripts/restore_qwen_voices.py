"""Point the accounts back at their real Qwen cloned voices."""

from app.db import SessionLocal
from app.models import User, VoiceClone

TARGETS = {
    # email -> (voice_id, preferred prefix)
    "owner@testdesk.local": ("qwen-omni-vc-Jordan_H_4f18-voice-20261017012445717-3922", "Jordan_H"),
    "gavin.j.smith0@googlemail.com": ("qwen-omni-vc-Gavin_0b39-voice-20261017014902539-b63f", "Gavin"),
}

db = SessionLocal()
for email, (voice_id, prefix) in TARGETS.items():
    user = db.query(User).filter(User.email == email).first()
    if not user:
        print("no user", email)
        continue
    clone = user.voice or VoiceClone(user_id=user.id)
    clone.voice_id = voice_id
    clone.target_model = "qwen3.8-omni-flash-realtime"
    clone.preferred_name = prefix
    if user.voice is None:
        db.add(clone)
    db.commit()
    print(f"{email}: -> {clone.voice_id} (delivery kept: {(clone.delivery or '')[:40]}…)")
db.close()