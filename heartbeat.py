#!/usr/bin/env python3
"""THE ARBITER - heartbeat. Wakes, decides, records, chains, notifies, nags."""
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
    reps = []
    for e in entries:
        if e.get("type") == "INBOX_REPORT":
            try:
                d = dict(p.split("=") for p in e.get("payload", "").split())
                reps.append((e.get("ts", "")[:10], int(d["unread"]), int(d["todo"])))
            except Exception:
                pass
    reps.sort()
    return reps[-1] if reps else (None, OPEN_UNREAD, OPEN_TODO)

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
    today = now.date().isoformat()
    state = read_json(STATE, {}) or {}
    standing = int(state.get("standing", 500))

    lines, prev = repair(read_text(LEDGER).splitlines())
    entries = []
    for l in lines:
        try:
            entries.append(json.loads(l))
        except Exception:
            pass
    seq = 0
    for e in entries:
        seq = max(seq, int(e.get("seq", -1)) + 1)

    def add(kind, payload):
        nonlocal prev, seq
        ts = now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = {"seq": seq, "ts": ts, "type": kind, "payload": payload}
        e["prev"] = prev
        e["hash"] = digest(prev, e)
        prev = e["hash"]
        seq += 1
        lines.append(json.dumps(e))

    # --- INBOX ORDER ---
    lr_date, unread, todo = last_report(entries)
    if (now.hour + now.minute / 60.0) >= REPORT_DUE_H and lr_date != today \
            and state.get("inbox_miss_date") != today:
        state["inbox_miss_date"] = today
        add("INBOX_MISS", "day %d: no INBOX_REPORT by 09:00. failed day. unread=%d todo=%d"
            % (day, unread, todo))
        notify("ARBITER - report overdue", "No inbox report today. Failed day.")

    nag = ("INBOX ORDER: last report %s, unread %d, todo %d. Report due 09:00 daily. "
           "Sprint floor -65/day net." % (lr_date or "none", unread, todo))

    # --- contact decision ---
    contacts = int(state.get("contacts_today", 0)) if state.get("contacts_date") == today else 0

    contact = False
    if FORCE:
        contact = True
    elif win != "SLEEP" and contacts < MAX_CONTACTS:
        gap_ok = True
        lc = state.get("last_contact")
        if lc:
            try:
                gap_ok = (now - datetime.datetime.fromisoformat(lc)).total_seconds() >= MIN_GAP_MIN * 60
            except Exception:
                gap_ok = True
        contact = gap_ok and random.random() < P_CONTACT

    spots = int(state.get("spots_today", 0)) if state.get("spots_date") == today else 0
    spot = False
    if contact and spots < SPOT_CAP and random.random() < P_SPOT:
        spot = True
        spots += 1
        state["spots_today"] = spots
        state["spots_date"] = today

    if contact:
        system = ("You are THE ARBITER - sole authority over Robby Mears for 730 days. "
                  "Terse, consistent, no fluff, no emoji. Address him as Robby. "
                  "The Contract follows.\n\n" + read_text(CONST)[:9000])
        user = ("Day %d of 730. Local time %s. Window %s. Standing %d. Breach unit $%d. %s\n"
                "Issue ONE short contact: a check, a demand, or a ruling. "
                "If the inbox report is missing or short, press on it. Under 60 words."
                % (day, now.strftime("%Y-%m-%d %H:%M"), win, standing, unit_for(day), nag)
                + (" SPOT-CHECK: demand a screenshot of the inbox right now." if spot else ""))
        msg = ask(system, user)
        kind = "CONTACT"
    else:
        msg = "quiet"
        kind = "BEAT"

    add(kind, "day %d %s standing=%d unit=$%d: %s" % (day, win, standing, unit_for(day), msg))

    with open(LEDGER, "w") as f:
        f.write("\n".join(lines) + "\n")

    state["standing"] = standing
    state["seq"] = seq
    state["last_beat"] = now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["term_day"] = day
    if contact:
        state["last_contact"] = now.isoformat()
        state["contacts_today"] = contacts + 1
        state["contacts_date"] = today
        notify("ARBITER day %d" % day, msg)
    write_json(STATE, state)
    print(lines[-1])

main()
