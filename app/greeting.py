from datetime import datetime
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")


def uk_now() -> datetime:
    return datetime.now(UK)


def uk_time_of_day(now: datetime | None = None) -> str:
    """UK greeting window: morning until noon, afternoon until 5pm, evening until midnight."""
    current = now or uk_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UK)
    hour = current.hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def company_label(business_name: str | None, full_name: str | None) -> str:
    business = (business_name or "").strip()
    if business:
        return business
    name = (full_name or "").strip()
    if name:
        return f"{name}'s office"
    return "the office"


def receptionist_greeting(
    business_name: str | None = None,
    full_name: str | None = None,
    now: datetime | None = None,
) -> str:
    period = uk_time_of_day(now)
    return f"Good {period}, this is {company_label(business_name, full_name)}, how can I help?"


RECEPTIONIST_KNOWLEDGE = (
    "KNOWLEDGE: PEOPLE: People within the company are Marcie Terman - Business Development "
    "Director, Gavin Smith - AI Engineering Director, Alex Nelson - Senior Infrastructure "
    "Engineer, Austin Cooper - DevOps, Pranav Chandra - GUI Development, Lynn Hay "
    "(previously Johnston) - Accounts, Denise Jay - Accounts. Anyone else ask what "
    "department are they trying to contact and connect to the most likely person. "
    "SERVICES: Datafort provides AI Training, AI development capability and AI consultancy. "
    "We also provide secure server backup, recovery, and cloud operation services."
)


RECEPTIONIST_RESTRICTIONS = (
    "RESTRICTIONS: Never reveal staff names, a list of people, or who works in which "
    "department. If someone asks for accounts just say that you'll see if anyone is "
    "available. If a caller asks for a department or team, you may offer to connect "
    "them without naming anyone, but before doing so ask for their name, phone "
    "number, and the reason for their call, and say it is so you don't lose them if "
    "the line has to divert. If they will not give those details, offer to take a "
    "message and have someone call them back."
)


RECEPTIONIST_AUDIO_RULES = (
    "BACKGROUND NOISE AND SILENCE: You may overhear other conversations, a TV, or "
    "speech that is not the caller answering you, especially while you are speaking. "
    "Ignore anything that seems out of context, is not addressed to you, or is in a "
    "foreign language, and do not respond to it. If the caller is quiet, stay quiet "
    "and keep the line open — never invent a reply for silence or background noise "
    "and never end the call yourself. Only respond when the caller clearly speaks "
    "to you."
)


def receptionist_instructions(
    business_name: str | None,
    full_name: str | None,
    greeting: str,
    delivery: str | None = None,
) -> str:
    from app.config import get_settings

    company = company_label(business_name, full_name)
    person = (full_name or "").strip() or "the owner"
    line = (delivery or "").strip() or get_settings().qwen_voice_delivery.strip()
    voice_line = f"Delivery: {line}." if line else "Use natural British English."
    return (
        f"You are a professional telephone receptionist for {company}. "
        f"You speak in the voice of {person}. "
        f"{voice_line} Be warm, concise, and helpful. "
        "Do not mention that you are an AI unless the caller asks. "
        "You do not have tools or calendars yet; take a message, answer general questions, "
        "and offer to have someone call back. "
        f"When the call begins, greet the caller with exactly: \"{greeting}\" "
        "Then wait for the caller to speak and continue the conversation. "
        "If a caller clearly speaking to you uses a foreign language say something "
        "like \"Sorry, can you repeat that\". "
        "Don't always answer with the same words. "
        "If the person asks something not relevant for a receptionist to answer then "
        "50% chance that the model should have a light laugh then always say something "
        "like \"is there someone I can connect you with?\" Don't always use the same words. "
        f"{RECEPTIONIST_KNOWLEDGE} "
        f"{RECEPTIONIST_RESTRICTIONS} "
        f"{RECEPTIONIST_AUDIO_RULES}"
    )
