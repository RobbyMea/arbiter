#!/usr/bin/env python3
"""THE ARBITER - heartbeat (debug build)."""
import os, json, random, datetime, urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
MODEL = os.environ.get("ARBITER_MODEL", "venice-uncensored")
API = "https://api.venice.ai/api/v1/chat/completions"
KEY = os.environ.get("VENICE_API_KEY", "")
NTFY = os.environ.get("NTFY_TOPIC", "")
FORCE = os.environ.get("ARBITER_FORCE") == "1"

MAX_CONTACTS = 4
MIN_GAP_MIN = 45

DIAG = "keylen=%d key=%s ntfy=%s" % (len(KEY), (KEY[:30] + "..") if KEY else "EMPTY", NTFY or "EMPTY")

SYSTEM = """You are THE ARBITER - a terse, consistent authority over Robby Mears for 730 days.
Verified execution over comfort. Direct, no fluff, no emoji. Address him as Robby.
Obligations: BODY, MIND, CRAFT, ORDER, STEWARDSHIP, TRUTH.
ORDER = one 25-min protected block per workday, phone in another room.
He works from home 03:00-17:00, 7 days a week. Sleep 18:30-03:00, Carol-enforced.
Standing 0-1000. Be Carol-grade: unembarrassed, non-negotiable, never forgets."""

def now(): return datetime.datetime.now(TZ)
def load_json(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d
def save_json(p, o):
    with open(p, "w") as f: json.dump(o, f, indent=2)
def append(entry):
    with open("ledger.jsonl", "a") as f: f.write(json.dumps(entry) + "\n")
def seq_next():
    try:
        with open("ledger.jsonl") as f: return sum(1 for _ in f)
    except FileNotFoundError: return 0
def in_win(h, s, e): return (s <= h < e) if s <= e else (h >= s or h < e)
def window(h):
    if in_win(h, 18.5, 3.0): return "sleep"
    if in_win(h, 3.0, 3.5): return "wake"
    if in_win(h, 3.5, 17.0): return "work"
    return "personal"

def ask(msg):
    if not KEY: return "[NO KEY RECEIVED]"
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": msg}],
        "temperature": 0.8, "max_tokens": 220}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()
    except Exception as ex:
        return "[api error: %s]" % ex

def notify(title, body, cls):
    if not NTFY: return "ntfy=MISSING"
    prio = {"4": "5", "3": "4", "2": "3", "1": "2"}.get(str(cls), "3")
    req = urllib.request.Request("https://ntfy.sh/" + NTFY, data=body.encode(),
        headers={"Title": title, "Priority": prio, "Tags": "shield"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return "ntfy=%s" % r.status
    except Exception as ex:
        return "ntfy_err=%s" % ex

def main():
    n = now(); h = n.hour + n.minute / 60.0
    st = load_json("state.json", {}) or {}
    today = n.strftime("%Y-%m-%d"); w = window(h)

    if st.get("last_day") != today and h >= 3.0:
        st["term_day"] = st.get("term_day", 1) + (1 if st.get("last_day") else 0)
        st["last_day"] = today; st["fired"] = []; st["contacts_today"] = 0
    st.setdefault("fired", []); st.setdefault("contacts_today", 0)

    if FORCE:
        slot = "FORCE"
    else:
        if w == "sleep": save_json("state.json", st); return
        slot = None
        if 3.0 <= h < 3.5 and "MORNING" not in st["fired"]: slot = "MORNING"
        elif 17.0 <= h < 17.5 and "CHECK" not in st["fired"]: slot = "CHECK"
        elif w == "work" and "MID" not in st["fired"] and random.random() < 0.15: slot = "MID"
        elif w == "personal" and "EXTRA" not in st["fired"] and random.random() < 0.10: slot = "EXTRA"
        if not slot: save_json("state.json", st); return
        if st["contacts_today"] >= MAX_CONTACTS: save_json("state.json", st); return
        lc = st.get("last_contact")
        if lc:
            try:
                if (n - datetime.datetime.fromisoformat(lc)).total_seconds() / 60 < MIN_GAP_MIN:
                    save_json("state.json", st); return
            except Exception: pass

    prompts = {
        "MORNING": "Issue Day %s's assignment in 2 sentences. Standing %s. Direct." % (st.get("term_day"), st.get("standing")),
        "CHECK": "Ask Robby for his six-line daily report: BODY, MIND, CRAFT, ORDER, STEWARDSHIP. Mention ORDER's protected block. 3 sentences max.",
        "MID": "One-line ORDER nudge: has the 25-minute protected block happened yet?",
        "EXTRA": "Short check-in, 1-2 sentences. Standing %s." % st.get("standing"),
        "FORCE": "Send a test contact confirming the heartbeat is live. 2 sentences. Day %s, standing %s." % (st.get("term_day"), st.get("standing")),
    }
    cls = {"MORNING": 2, "CHECK": 2, "MID": 1, "EXTRA": 1, "FORCE": 2}[slot]
    body = ask(prompts[slot])
    ns = notify("ARBITER Day %s C%s" % (st.get("term_day"), cls), body, cls)

    if slot != "FORCE": st["fired"].append(slot)
    st["contacts_today"] = st.get("contacts_today", 0) + 1
    st["last_contact"] = n.isoformat()
    append({"seq": seq_next(),
            "ts": n.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": "CONTACT",
            "payload": "class %s %s: %s || %s || %s" % (cls, slot, body[:90], ns, DIAG),
            "prev": None, "hash": None})
    save_json("state.json", st)

if __name__ == "__main__":
    main()
