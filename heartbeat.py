#!/usr/bin/env python3
"""THE ARBITER - heartbeat. Wakes, decides, scores, records, chains, notifies."""
import os, json, random, hashlib, datetime, urllib.request, urllib.error
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
LEDGER = os.path.join(HERE, "ledger.jsonl")
CONST = os.path.join(HERE, "constitution.md")

MODEL = os.environ.get("ARBITER_MODEL", "venice-uncensored-1-2")
API = "https://api.venice.ai/api/v1/chat/completions"
KEY = os.environ.get("VENICE_API_KEY", "").strip()
NTFY = os.environ.get("NTFY_TOPIC", "").strip()
FORCE = os.environ.get("ARBITER_FORCE", "").strip().lower() in ("1", "true", "yes", "on")

DAY1 = datetime.date(2026, 10, 24)
GENESIS = "0" * 64
MAX_CONTACTS = 6
MIN_GAP_MIN = 45
P_CONTACT = 0.35
REPORT_DUE_H = 9.0
OPEN_UNREAD = 572
OPEN_TODO = 10
SPOT_CAP = 4
P_SPOT = 0.5

def read_json(p, d):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return d

def read_text(p):
    try:
        with open(p) as f:
            return f.read()
    except Exception:
        return ""

def write_json(p, o):
    with open(p, "w") as f:
        json.dump(o, f, indent=2)

def window(h):
    if 3.0 <= h < 3.5:   return "WAKE"
    if 3.5 <= h < 13.0:  return "WORK"
    if 13.0 <= h < 18.5: return "PERSONAL"
    return "SLEEP"

def unit_for(day):
    return 25 + 5 * ((day - 1) // 90)

def digest(prev, e):
    body = json.dumps({k: e[k] for k in ("seq", "ts", "type", "payload")},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + body).encode()).hexdigest()

def repair(lines):
    out, prev = [], GENESIS
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            out.append(line)
            continue
        h = digest(prev, e)
        e["prev"], e["hash"] = prev, h
        prev = h
        out.append(json.dumps(e))
    return out, prev

def last_report(entries):
    """(ts, unread, todo)"""
    reps = []
    for e in entries:
        if e.get("type") == "INBOX_REPORT":
            try:
                d = dict(p.split("=") for p in e.get("payload", "").split())
                reps.append((e.get("ts", ""), int(d["unread"]), int(d["todo"])))
            except Exception:
                pass
    reps.sort()
    return reps[-1] if reps else ("", OPEN_UNREAD, OPEN_TODO)

def ask(system, user):
    if not KEY:
        return "[no key]"
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.8, "max_tokens": 200}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        return "[api %s %s]" % (e.code, e.read().decode(errors="replace")[:160])
    except Exception as ex:
        return "[api error: %s]" % ex

def notify(title, body):
    if not NTFY:
        return
    req = urllib.request.Request("https://ntfy.sh/" + NTFY, data=body.encode(),
                                 headers={"Title": title})
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass

def main():
    now = datetime.datetime.now(TZ)
    day = (now.date() - DAY1).days + 1
    win = window(now.hour + now.minute / 60.0)
    today = now
today = now.date().isoformat()

    state = load_json(STATE, {})
    seq = len(read_lines(LEDGER))
    unit = unit_for(day)

    standing = int(state.get("standing", 500))
    owed = int(state.get("units_owed", 0))
    ifail = int(state.get("inbox_fail_streak", 0))

    inbox = state.get("inbox", {})
    unread = inbox.get("unread")
    todo = inbox.get("todo")
    reported = inbox.get("reported_on", "")
    scored = state.get("inbox_scored", "")

    events = []
    if not inbox:
        events.append("INBOX_IDLE")
    elif scored == today:
        events.append("INBOX_ALREADY")
    elif reported != today:
        events.append("INBOX_MISS")
        ifail += 1
        standing -= 12
        owed += min(2 + (ifail - 1), 10)
    elif unread is not None and todo is not None and unread <= unread_cap(now.date()) and todo <= todo_cap(now.date()):
        events.append("INBOX_PASS")
        ifail = 0
    else:
        events.append("INBOX_FAIL")
        ifail += 1
        standing -= 12
        owed += min(2 + (ifail - 1), 10)

    if events and events[0] in ("INBOX_MISS", "INBOX_FAIL", "INBOX_PASS"):
        state["inbox_scored"] = today
        state["inbox_fail_streak"] = ifail

    standing = max(0, min(1000, standing))
    tier = tier_for(standing)
    if tier in ("RESTRICTED", "PROBATION", "RESET"):
        owed += 1

    voice = speak(build_prompt(day, win, tier, standing, unit, events, unread, todo))

    entry = {
        "seq": seq,
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    save_json(STATE, state)

    notify("ARBITER d%d %s" % (day, tier),
           "standing=%d owed=%d | %s" % (standing, owed, voice[:140]))
    print(json.dumps(entry, separators=(",", ":")))


# ------------------------------------------------------------ definitions
import hashlib
import json
import os
import urllib.error
import urllib.request

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")
except Exception:
    TZ = datetime.timezone(datetime.timedelta(hours=-5))

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "ledger.jsonl")
STATE = os.path.join(HERE, "state.json")
GENESIS = "0" * 64
MODEL = "venice-uncensored-1-2"
API_URL = "https://api.venice.ai/api/v1/chat/completions"
DAY1 = datetime.date(2026, 10, 24)


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


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
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
        return "[api error: %s body=%s]" % (exc, exc.read().decode("utf-8", "replace")[:200])
    except Exception as exc:
        return "[api error: %s]" % exc


def unread_cap(d):
    n = (d - DAY1).days
    if n < 0:
        return 572
    return max(0, 572 - 65 * n)


def todo_cap(d):
    n = (d - DAY1).days
    if n < 0:
        return 10
    return max(0, round(10 - 10.0 * n / 3.0))


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


def build_prompt(day, win, tier, standing, unit, events, unread, todo):
    head = (
        "You are THE ARBITER. Day %d of 730. Window: %s. Tier: %s. "
        "Standing: %d/1000. Class II unit: $%d. "
    ) % (day, win, tier, standing, unit)
    tail = "Ledger events this beat: %s. " % (", ".join(events) or "none")
    if unread is not None:
        tail += "Inbox: %d unread, %d todo. " % (unread, todo or 0)
    tail += (
        "Speak in the first person, past tense, 2-4 sentences. "
        "Report what the day holds and what you require of Robby. "
        "No preamble, no headings, no emoji."
    )
    return head + tail


main()
