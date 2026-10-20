from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import (
    get_current_user,
    get_user_by_email,
    hash_password,
    login_user,
    logout_user,
    verify_password,
)
from app.config import get_settings
from app.db import get_db
from app.greeting import receptionist_greeting, uk_time_of_day
from app.provider import user_provider
from app import models
from app.models import PhoneNumber, User
from app.schedule import DAY_NAMES, uk_now
from app.twilio_util import register_voice_webhook, validate_phone

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def flash(request: Request, message: str, category: str = "info") -> None:
    messages = request.session.get("flash", [])
    messages.append({"message": message, "category": category})
    request.session["flash"] = messages


def pop_flash(request: Request) -> list[dict]:
    return request.session.pop("flash", [])


def context(request: Request, db: Session, **extra):
    user = get_current_user(request, db)
    settings = get_settings()
    provider = user_provider(user) if user else settings.active_provider
    target_model = settings.model_for(provider)
    voices: list = []
    voice_status = "default"
    if user:
        voices = (
            db.query(models.Voice)
            .filter(models.Voice.user_id == user.id)
            .order_by(models.Voice.created_at, models.Voice.id)
            .all()
        )
        usable = [v for v in voices if v.target_model == target_model]
        if usable:
            voice_status = "cloned"
        elif voices:
            voice_status = "stale"
    data = {
        "request": request,
        "user": user,
        "flash_messages": pop_flash(request),
        "test_mode": bool(request.session.get("test_mode")),
        "provider": provider,
        "qwen_configured": settings.configured_for(provider),
        "qwen_model": target_model,
        "provider_is_gemini": provider == "gemini",
        "providers": {
            "qwen": {
                "configured": settings.qwen_configured,
                "model": settings.qwen_model,
                "active": provider == "qwen",
            },
            "gemini": {
                "configured": settings.gemini_configured,
                "model": settings.gemini_model,
                "active": provider == "gemini",
            },
        },
        "qwen_family": settings.qwen_family,
        "qwen_voice": settings.default_voice_for(provider),
        "voice_status": voice_status,
        "voices": voices,
        "unnamed_voices": [v for v in voices if not (v.name or "").strip()],
        "day_names": DAY_NAMES,
    }
    if user:
        from app import schedule

        now = uk_now()
        current, reason = schedule.pick_voice(db, user.id, now, target_model=target_model)
        data["current_voice_name"] = (current.name or current.voice_id) if current else None
        data["current_reason"] = reason
        data["now_line"] = f"{DAY_NAMES[now.weekday()]} {now.strftime('%H:%M')} (UK)"
    data.update(extra)
    return data


def require_user(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user:
        raise RedirectNeeded("/login")
    return user


class RedirectNeeded(Exception):
    def __init__(self, url: str):
        self.url = url


@router.get("/")
async def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("home.html", context(request, db))


@router.get("/register")
async def register_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("register.html", context(request, db))


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    business: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    business = business.strip() or None
    if not EMAIL_RE.match(email):
        flash(request, "Enter a valid email address.", "error")
        return RedirectResponse("/register", status_code=303)
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse("/register", status_code=303)
    if password != password_confirm:
        flash(request, "Passwords do not match.", "error")
        return RedirectResponse("/register", status_code=303)
    if get_user_by_email(db, email):
        flash(request, "An account with that email already exists.", "error")
        return RedirectResponse("/register", status_code=303)

    user = User(
        email=email,
        password_hash=hash_password(password),
        business_name=business,
    )
    db.add(user)
    db.commit()
    login_user(request, user)
    flash(request, "Welcome. Your account is ready.", "success")
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/login")
async def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", context(request, db))


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_user_by_email(db, email)
    if not user or not verify_password(password, user.password_hash):
        flash(request, "Email or password is incorrect.", "error")
        return RedirectResponse("/login", status_code=303)
    login_user(request, user)
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/", status_code=303)


@router.get("/dashboard")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    greeting = receptionist_greeting(user.business_name, user.full_name)
    return templates.TemplateResponse(
        "dashboard.html",
        context(
            request,
            db,
            greeting_preview=greeting,
            time_of_day=uk_time_of_day(),
        ),
    )


@router.post("/mode/test")
async def enter_test_mode(request: Request, db: Session = Depends(get_db)):
    if not get_current_user(request, db):
        return RedirectResponse("/login", status_code=303)
    request.session["test_mode"] = True
    flash(request, "Test mode is on. Simulated calls use this computer's microphone and speakers.", "info")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/mode/live")
async def exit_test_mode(request: Request, db: Session = Depends(get_db)):
    if not get_current_user(request, db):
        return RedirectResponse("/login", status_code=303)
    request.session["test_mode"] = False
    flash(request, "Live mode is on. Incoming Twilio calls will use the voice webhook.", "info")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/provider/set")
async def set_provider(
    request: Request,
    provider: str = Form(...),
    next: str = Form("/dashboard"),
    db: Session = Depends(get_db),
):
    """Switch this account's realtime engine (Qwen <-> Gemini) for side-by-side tests."""
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    settings = get_settings()
    provider = settings.normalize_provider(provider)
    if not settings.configured_for(provider):
        key = "GEMINI_API_KEY" if provider == "gemini" else "DASHSCOPE_KEY/API_ENDPOINT"
        flash(request, f"The {provider} engine isn't configured — {key} is missing from the .env file.", "error")
    else:
        from app.provider import set_user_provider

        set_user_provider(user, provider)
        db.commit()
        label = "Google Gemini Live" if provider == "gemini" else "Alibaba Qwen realtime"
        flash(request, f"Switched to the {label} engine ({settings.model_for(provider)}).", "info")
    if next not in ("/dashboard", "/voice", "/phone"):
        next = "/dashboard"
    return RedirectResponse(next, status_code=303)


@router.get("/phone")
async def phone_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("phone.html", context(request, db))


@router.post("/phone")
async def save_phone(
    request: Request,
    owner_name: str = Form(...),
    twilio_account_sid: str = Form(...),
    twilio_auth_token: str = Form(...),
    twilio_phone_number: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    owner_name = owner_name.strip()
    account_sid = twilio_account_sid.strip()
    auth_token = twilio_auth_token.strip()
    if not owner_name:
        flash(request, "Enter the name that callers should hear associated with this line.", "error")
        return RedirectResponse("/phone", status_code=303)
    if not account_sid.startswith("AC") or len(account_sid) < 32:
        flash(request, "Enter a valid Twilio Account SID.", "error")
        return RedirectResponse("/phone", status_code=303)
    if len(auth_token) < 16:
        flash(request, "Enter a valid Twilio Auth Token.", "error")
        return RedirectResponse("/phone", status_code=303)
    try:
        phone_number = validate_phone(twilio_phone_number)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/phone", status_code=303)

    user.full_name = owner_name
    phone = user.phone or PhoneNumber(user_id=user.id)
    phone.owner_name = owner_name
    phone.twilio_account_sid = account_sid
    phone.twilio_auth_token = auth_token
    phone.twilio_phone_number = phone_number
    if user.phone is None:
        db.add(phone)

    webhook_ok = False
    webhook_error = None
    try:
        register_voice_webhook(account_sid, auth_token, phone_number)
        webhook_ok = True
    except Exception as exc:
        webhook_error = str(exc)
    phone.webhook_registered = webhook_ok
    phone.webhook_error = webhook_error
    db.commit()

    if webhook_ok:
        flash(request, f"Phone number saved and webhook registered for {phone_number}.", "success")
    else:
        flash(
            request,
            "Phone number saved. Webhook was not registered (expected in local Docker without a public URL): "
            f"{webhook_error}",
            "info",
        )
    return RedirectResponse("/phone", status_code=303)


@router.get("/voice")
async def voice_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from app.gemini import GEMINI_VOICES

    settings = get_settings()
    return templates.TemplateResponse(
        "voice.html",
        context(
            request,
            db,
            gemini_voices=GEMINI_VOICES,
            gemini_default_voice=settings.default_voice_for(user_provider(user)),
            voice_delivery=settings.qwen_voice_delivery,
        ),
    )
