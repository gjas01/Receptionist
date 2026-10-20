from __future__ import annotations

import logging
import re

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import get_settings

log = logging.getLogger(__name__)
E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalise_phone(number: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", (number or "").strip())
    if cleaned and not cleaned.startswith("+"):
        digits = re.sub(r"\D", "", cleaned)
        if digits.startswith("00"):
            digits = digits[2:]
        cleaned = f"+{digits}"
    return cleaned


def validate_phone(number: str) -> str:
    normalised = normalise_phone(number)
    if not E164.match(normalised):
        raise ValueError("Enter the Twilio number in E.164 format, for example +441234567890.")
    return normalised


def register_voice_webhook(account_sid: str, auth_token: str, phone_number: str) -> None:
    settings = get_settings()
    voice_url = f"{settings.public_base_url.rstrip('/')}/webhook/voice"
    client = Client(account_sid, auth_token)
    try:
        matches = client.incoming_phone_numbers.list(phone_number=phone_number)
        if not matches:
            raise RuntimeError(
                f"Twilio account does not contain the number {phone_number}."
            )
        matches[0].update(
            voice_url=voice_url,
            voice_method="POST",
            status_callback=None,
        )
    except TwilioRestException as exc:
        detail = exc.msg or str(exc)
        raise RuntimeError(f"Twilio rejected the webhook update: {detail}") from exc
    log.info("Registered Twilio voice webhook for %s -> %s", phone_number, voice_url)


def hangup_call(account_sid: str, auth_token: str, call_sid: str) -> None:
    """End an in-progress Twilio call leg (used by the silence guard)."""
    if not call_sid:
        return
    client = Client(account_sid, auth_token)
    try:
        client.calls(call_sid).update(status="completed")
        log.info("Hung up Twilio call %s", call_sid)
    except TwilioRestException as exc:
        log.warning("Could not hang up Twilio call %s: %s", call_sid, exc)
