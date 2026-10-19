from __future__ import annotations

import html
import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import PhoneNumber
from app.twilio_util import normalise_phone

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/voice")
async def incoming_call(request: Request, db: Session = Depends(get_db)):
    """Twilio voice webhook: connect the caller to our Qwen realtime bridge."""
    form = await request.form()
    to_number = normalise_phone(str(form.get("To") or ""))
    from_number = str(form.get("From") or "")
    settings = get_settings()

    phone = None
    if to_number:
        phone = db.query(PhoneNumber).filter(PhoneNumber.twilio_phone_number == to_number).first()
    stream_url = html.escape(f"{settings.public_ws_base}/ws/twilio", quote=True)
    to_attr = html.escape(to_number, quote=True)
    from_attr = html.escape(from_number, quote=True)
    log.info("Incoming Twilio call To=%s From=%s user=%s", to_number, from_number, getattr(phone, "user_id", None))

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{stream_url}">
      <Parameter name="to" value="{to_attr}" />
      <Parameter name="from" value="{from_attr}" />
    </Stream>
  </Connect>
</Response>
"""
    return Response(content=twiml, media_type="application/xml")
