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
