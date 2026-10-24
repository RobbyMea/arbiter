#!/usr/bin/env python3
"""
THE ARBITER - autonomous heartbeat.

Runs from GitHub Actions on a schedule. Day 1 = 2026-10-24. Term = 730 days.
Writes ledger.jsonl (SHA-256 hash chained) and state.json, pushes a
notification, and speaks in its own words via the Venice API.

Public repo = persistence. The chain is repaired on every run so a single
bad line can never silently break it.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")
except Exception:
    TZ = timezone(timedelta(hours=-5))


# --------------------------------------------------------------------- config
HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "ledger.jsonl")
STATE = os.path.join(HERE, "state.json")
INBOX = os.path.join(HERE, "inbox.json")
CONSTITUTION = os.path.join(HERE, "constitution.md")

DAY1 = date(2026, 10, 24)
TERM_DAYS = 730
GENESIS = "0" * 64

MODEL = os.environ.get("ARBITER_MODEL", "venice-uncensored-1-2")
API_URL = "https://api.venice.ai/api/v1/chat/completions"

SPRINT_START = date(2026, 10, 24)
SPRINT_UNREAD0 = 572
SPRINT_RATE = 65
TODO0 = 10
TODO_ZERO = date(2026, 10, 27)
MISS_HOUR = 12


# ----------------------------------------------------------------------- time
def now():
    return datetime.now(TZ)


def window(hour):
    if 3 <= hour < 6:
        return "WAKE"
    if 6 <= hour < 17:
        return "WORK"
    if 17 <= hour < 19:
        return "PERSONAL"
    return "SLEEP"


def unit_for(day):
    return 25 + 5 * ((day - 1) // 90)


def unread_cap(d):
    n = (d - SPRINT_START).days
    if n < 0:
        return SPRINT_UNREAD0
    return max(0, SPRINT_UNREAD0 - SPRINT_RATE * n)


def todo_cap(d):
    if d >= TODO_ZERO:
        return 0
    n = (d - SPRINT_START).days
    if n < 0:
        return TODO0
    span = (TODO_ZERO - SPRINT_START).days
    return max(0, round(TODO0 * (1.0 - n / float(span))))


def tier_for(standing):
    if standing >= 800:
        return "ASCENDANT"
    if standing >= 500:
        return "STANDARD"
    if standing >= 250:
        return "RESTRICTED"
    if standing >= 1:
        return "PROBATION"
    return "RESET"


# ------------------------------------------------------------------- file io
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")


def read_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return [l for l in fh.read().splitlines() if l.strip()]
    except Exception:
        return []


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- hash chain
def repair(lines):
    out = []
    prev = GENESIS
    for raw in lines:
        try:
            obj = json.loads(raw)
        except Exception:
            out.append(raw)
            continue
        obj.pop("prev", None)
        obj.pop("hash", None)
        obj["prev"] = prev
        body = json.dumps(obj, sort_keys=True, separators=(",", ":"))
        obj["hash"] = sha(prev + body)
        prev = obj["hash"]
        out.append(json.dumps(obj, separators=(",", ":")))
    return out, prev


def append(entry):
    lines = read_lines(LEDGER)
    lines.append(json.dumps(entry, separators=(",", ":")))
    lines, head = repair(lines)
    write_lines(LEDGER, lines)
    return head


# ---------------------------------------------------------------- outward io
def notify(title, body):
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        return "no-topic"
    req = urllib.request.Request(
        "https://ntfy.sh/" + topic,
        data=body.encode("utf-8"),
        headers={"Title": title},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status
    except Exception as exc:
        return "err:%s" % exc


def speak(prompt):
    key = os.environ.get("VENICE_API_KEY", "")
    if not key:
        return "[no key]"
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0.8,
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        return "[api error: %s body=%s]" % (exc, body)
    except Exception as exc:
        return "[api error: %s]" % exc


def constitution_head(limit=1200):
    try:
        with open(CONSTITUTION, "r", encoding="utf-8") as fh:
            return fh.read()[:limit]
    except Exception:
        return ""


# ---------------------------------------------------------------- the prompt
def build_prompt(day, win, tier, standing, unit, events, unread, todo, owed):
    parts = []
    head = constitution_head()
    if head:
        parts.append("CONTRACT EXCERPT:\n" + head)
    parts.append(
        "You are THE ARBITER. Day %d of %d. Window: %s. Tier: %s. "
        "Standing: %d/1000. Class II unit: $%d. Units owed: %d."
        % (day, TERM_DAYS, win, tier, standing, unit, owed)
    )
    parts.append("Events this beat: %s." % (", ".join(events) or "none"))
    if unread is not None:
        parts.append(
            "Inbox: %d unread (cap %d), %d todo (cap %d)."
            % (unread, unread_cap(now().date()), todo or 0, todo_cap(now().date()))
        )
    parts.append(
        "Speak in the first person, past tense, 2-4 sentences. State what "
        "stands and what you require of Robby. No preamble, no headings, "
        "no emoji."
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------- main
def main():
    ts = now()
    today = ts.date().isoformat()
    day = (ts.date() - DAY1).days + 1
    win = window(ts.hour + ts.minute / 60.0)

    if day < 1:
        print("term not started")
        return
    if day > TERM_DAYS:
        notify("ARBITER - term complete", "Day %d of %d." % (day, TERM_DAYS))
        return

    state = load_json(STATE, {})
    seq = len(read_lines(LEDGER))
    unit = unit_for(day)

    hour_key = ts.strftime("%Y-%m-%dT%H")
    quiet = state.get("last_beat_hour") == hour_key

    standing = int(state.get("standing", 500))
    owed = int(state.get("units_owed", 0))
    ifail = int(state.get("inbox_fail_streak", 0))
    scored = state.get("inbox_scored", "")

    rep = load_json(INBOX, {})
    rdate = str(rep.get("date", ""))
    unread = rep.get("unread")
    todo = rep.get("todo")

    events = []

    if scored == today:
        events.append("INBOX_ALREADY")
    elif rdate == today and unread is not None and todo is not None:
        if unread <= unread_cap(ts.date()) and todo <= todo_cap(ts.date()):
            events.append("INBOX_PASS")
            ifail = 0
        else:
            events.append("INBOX_FAIL")
            ifail += 1
            standing -= 12
            owed += min(2 + (ifail - 1), 10)
        state["inbox_scored"] = today
        state["inbox_fail_streak"] = ifail
    elif ts.hour >= MISS_HOUR:
        events.append("INBOX_MISS")
        ifail += 1
        standing -= 12
        owed += min(2 + (ifail - 1), 10)
        state["inbox_scored"] = today
        state["inbox_fail_streak"] = ifail
    else:
        events.append("INBOX_PENDING")

    standing = max(0, min(1000, standing))
    tier = tier_for(standing)

    if tier in ("RESTRICTED", "PROBATION", "RESET") and state.get("rider_day") != today:
        owed += 1
        state["rider_day"] = today

    if quiet:
        save_json(STATE, state)
        print("quiet")
        return

    state["last_beat_hour"] = hour_key

    voice = speak(build_prompt(day, win, tier, standing, unit, events, unread, todo, owed))

    entry = {
        "seq": seq,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "day": day,
        "window": win,
        "tier": tier,
        "standing": standing,
        "unit": unit,
        "events": events,
        "unread": unread,
        "todo": todo,
        "owed": owed,
        "voice": voice,
    }
    entry["hash"] = append(entry)

    state["standing"] = standing
    state["units_owed"] = owed
    state["last_beat"] = today
    state["day"] = day
    state["tier"] = tier
    save_json(STATE, state)

    notify(
        "ARBITER d%d %s" % (day, tier),
        "standing=%d owed=%d | %s" % (standing, owed, voice[:140]),
    )
    print(json.dumps(entry, separators=(",", ":")))


if __name__ == "__main__":
    main()
