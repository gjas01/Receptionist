"""End-to-end smoke test of the named-voices + schedule feature over HTTP."""

import json
import urllib.request
import http.cookiejar

BASE = "http://localhost:8000"

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def post(path, data: dict) -> tuple[int, dict]:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with opener.open(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw[:200].decode(errors="replace")}


def get(path) -> tuple[int, str]:
    with opener.open(BASE + path) as resp:
        return resp.status, resp.read().decode()


status, html = get("/register")
assert status == 200

import urllib.parse

form = urllib.parse.urlencode(
    {"email": "smoke@testdesk.local", "password": "smokepass123", "password_confirm": "smokepass123", "business": "Smoke Studio"}
).encode()
req = urllib.request.Request(BASE + "/register", data=form)
try:
    opener.open(req)
except urllib.error.HTTPError:
    pass
try:
    with opener.open(BASE + "/api/voices") as probe:
        ok = probe.status == 200
except urllib.error.HTTPError:
    ok = False
if not ok:  # account already existed -> log in
    login = urllib.parse.urlencode({"email": "smoke@testdesk.local", "password": "smokepass123"}).encode()
    opener.open(urllib.request.Request(BASE + "/login", data=login))

status, html = get("/voice")
assert status == 200, status
assert "Your voices" in html and "Answering schedule" in html, "voice page missing sections"
print("voice page OK (200, sections present)")

status, data = get("/dashboard")
assert "Smoke Studio" in data
print("dashboard OK")

# Empty schedule list
code, data = post("/api/voices", {}) if False else (None, None)
with opener.open(BASE + "/api/voices") as resp:
    info = json.loads(resp.read())
print("api/voices:", info["voices"], info["slots"], "reason:", info["reason"])
voice_ids = [v["id"] for v in info["voices"]]

# Save a schedule with two non-overlapping slots (random voice ids)
code, data = post(
    "/api/voice/slots",
    {
        "slots": [
            {"day": None, "start": "00:00", "end": "09:00", "voice_id": None},
            {"day": 0, "start": "09:00", "end": "12:00", "voice_id": None},
        ]
    },
)
assert code == 200, (code, data)
print("save schedule OK:", data["slots"])

# Overlap should be rejected (409)
code, data = post(
    "/api/voice/slots",
    {
        "slots": [
            {"day": 0, "start": "09:00", "end": "13:00", "voice_id": None},
            {"day": 0, "start": "12:00", "end": "15:00", "voice_id": None},
        ]
    },
)
assert code == 409, (code, data)
print("overlap rejected:", data["error"])

# All-days vs specific day overlap
code, data = post(
    "/api/voice/slots",
    {
        "slots": [
            {"day": None, "start": "09:00", "end": "13:00", "voice_id": None},
            {"day": 2, "start": "12:00", "end": "15:00", "voice_id": None},
        ]
    },
)
assert code == 409, (code, data)
print("all-days overlap rejected:", data["error"])

# Unknown voice in schedule
code, data = post("/api/voice/slots", {"slots": [{"day": None, "start": "09:00", "end": "10:00", "voice_id": 9999}]})
assert code == 400, (code, data)
print("unknown voice rejected:", data["error"])

# Restore the good schedule
code, data = post(
    "/api/voice/slots",
    {
        "slots": [
            {"day": None, "start": "00:00", "end": "09:00", "voice_id": None},
            {"day": 0, "start": "09:00", "end": "12:00", "voice_id": None},
        ]
    },
)
assert code == 200
print("final schedule restored:", data["slots"])

# Clean up: leave the smoke account with an empty (all random) schedule
code, data = post("/api/voice/slots", {"slots": []})
assert code == 200
print("schedule cleared:", data["slots"])

print("ALL SMOKE CHECKS PASSED")
