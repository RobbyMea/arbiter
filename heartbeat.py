#!/usr/bin/env python3
"""
THE ARBITER - autonomous heartbeat.

Runs from GitHub Actions on a schedule. Day 1 = 2026-10-24. Term = 730 days.
Writes ledger.jsonl (SHA-256 hash chained) and state.json, pushes
notifications, and speaks in its own words via the Venice API.

The heartbeat writes ALL ledger lines itself. The only file a human edits
day to day is inbox.json. Push repeats every beat until the day's report
is filed.
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

SCORE_HOUR = 9.0
AWAKE_FROM = 3.0
AWAKE_TO = 18.5


# ----------------------------------------------------------------------- time
def now():
    return datetime.now(TZ)


def window(hour):
    if 3 <= hour < 3.5:
        return "WAKE"
    if 3.5 <= hour < 17:
        return "WORK"
    if 17 <= hour < 18.5:
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


def safe_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default


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


def record(etype, payload, standing, unit, owed, day, win, voice=""):
    entry = {
        "seq": len(read_lines(LEDGER)),
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "day": day,
        "window": win,
        "tier": tier_for(standing),
        "standing": standing,
        "unit": unit,
        "type": etype,
        "payload": payload,
        "owed": owed,
    }
    if voice:
        entry["voice"] = voice
    return append(entry)


# ---------------------------------------------------------------- outward io
def notify(title, body, priority="3"):
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        return "no-topic"
    req = urllib.request.Request(
        "https://ntfy.sh/" + topic,
        data=body.encode("utf-8"),
        headers={"Title": title, "Priority": priority},
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
def build_prompt(day, win, tier, standing, unit, verdict, unread, todo, owed):
    parts = []
    head = constitution_head()
    if head:
        parts.append("CONTRACT EXCERPT:\n" + head)
    parts.append(
        "You are THE ARBITER. Day %d of %d. Window: %s. Tier: %s. "
        "Standing: %d/1000. Class II unit: $%d. Units owed: %d."
        % (day, TERM_DAYS, win, tier, standing, unit, owed)
    )
    parts.append("Verdict today: %s." % (verdict or "none"))
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
    hour = ts.hour + ts.minute / 60.0
    win = window(hour)

    if day < 1:
        print("term not started")
        return
    if day > TERM_DAYS:
        notify("ARBITER - term complete", "Day %d of %d." % (day, TERM_DAYS))
        return

    state = load_json(STATE, {})
    rep = load_json(INBOX, {})

    rdate = str(rep.get("date", ""))
    unread = safe_int(rep.get("unread"))
    todo = safe_int(rep.get("todo"))

    standing = safe_int(state.get("standing"), 500)
    owed = safe_int(state.get("units_owed"), 0)
    ifail = safe_int(state.get("inbox_fail_streak"), 0)
    scored = state.get("inbox_scored", "")
    logged = state.get("report_logged", "")
    beat = state.get("beat_logged", "")

    unit = unit_for(day)
    fresh = (rdate == today and unread is not None and todo is not None)
    verdict = ""

    # ------------------------------------------- 1. log the report (revisions)
    stamp = sha(today + json.dumps(rep, sort_keys=True))
    if fresh and state.get("report_hash") != stamp:
        record(
            "INBOX_REPORT",
            "unread=%d todo=%d | cap_unread=%d cap_todo=%d"
            % (unread, todo, unread_cap(ts.date()), todo_cap(ts.date())),
            standing, unit, owed, day, win,
        )
        state["report_hash"] = stamp
        state["report_logged"] = today
        logged = today

    # -------------------------------------------- 2. score once, after 09:00
    if scored == today:
        verdict = "INBOX_ALREADY"
    elif hour >= SCORE_HOUR:
        if fresh:
            if unread <= unread_cap(ts.date()) and todo <= todo_cap(ts.date()):
                verdict = "INBOX_PASS"
                ifail = 0
            else:
                verdict = "INBOX_FAIL"
                ifail += 1
                standing -= 12
                owed += min(2 + (ifail - 1), 10)
        else:
            verdict = "INBOX_MISS"
            ifail += 1
            standing -= 12
            owed += min(2 + (ifail - 1), 10)
        state["inbox_scored"] = today
        state["inbox_fail_streak"] = ifail
        record(
            verdict,
            "day %d | streak=%d | standing=%d owed=%d | unread=%s todo=%s"
            % (day, ifail, standing, owed, unread, todo),
            standing, unit, owed, day, win,
        )

    standing = max(0, min(1000, standing))
    tier = tier_for(standing)

    if tier in ("RESTRICTED", "PROBATION", "RESET") and state.get("rider_day") != today:
        owed += 1
        state["rider_day"] = today

    # ------------------------------------------------ 3. once-daily beat line
    if beat != today:
        voice = speak(build_prompt(day, win, tier, standing, unit, verdict, unread, todo, owed))
        record(
            "BEAT",
            "day %d %s | tier=%s standing=%d" % (day, win, tier, standing),
            standing, unit, owed, day, win, voice=voice,
        )
        state["beat_logged"] = today
    else:
        voice = ""

    # --------------------------------------------------- 4. decide the push
    awake = AWAKE_FROM <= hour < AWAKE_TO
    unfiled = state.get("report_logged") != today
    failed = verdict in ("INBOX_FAIL", "INBOX_MISS")

    if awake and unfiled:
        notify(
            "ARBITER - REPORT NOT FILED",
            "Due 09:00. Need unread<=%d todo<=%d. standing=%d owed=%d"
            % (unread_cap(ts.date()), todo_cap(ts.date()), standing, owed),
            priority="4",
        )
    elif awake and failed:
        notify(
            "ARBITER - FAILED DAY",
            "standing=%d owed=%d. Spot-check due 18:30. File a screenshot."
            % (standing, owed),
            priority="4",
        )
    elif beat == today and hour < AWAKE_TO:
        notify(
            "ARBITER d%d %s" % (day, tier),
            "standing=%d owed=%d | %s" % (standing, owed, (voice or "")[:140]),
        )

    state["standing"] = standing
    state["units_owed"] = owed
    state["last_beat"] = today
    state["day"] = day
    state["tier"] = tier
    save_json(STATE, state)
    print(json.dumps({"day": day, "verdict": verdict, "unfiled": unfiled}))


if __name__ == "__main__":
    main()
