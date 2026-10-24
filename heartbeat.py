#!/usr/bin/env python3
"""THE ARBITER - heartbeat (DEBUG BUILD 2). Prints full key + exact API response body."""
import os, json, datetime, urllib.request, urllib.error
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
MODEL = os.environ.get("ARBITER_MODEL", "venice-uncensored")
API = "https://api.venice.ai/api/v1/chat/completions"
MODELS = "https://api.venice.ai/api/v1/models"
KEY = os.environ.get("VENICE_API_KEY", "").strip()
NTFY = os.environ.get("NTFY_TOPIC", "").strip()

SYSTEM = ("You are THE ARBITER, a terse authority over Robby Mears for 730 days. "
          "Direct, no fluff, no emoji. Address him as Robby.")

def load(p, d=None):
    try:
        with open(p) as f:
            return json.load(f) if p.endswith(".json") else f.read()
    except Exception:
        return d

def next_seq():
    txt = load("ledger.jsonl", "") or ""
    mx = -1
    for line in txt.splitlines():
        if not line.strip():
            continue
        try:
            mx = max(mx, int(json.loads(line).get("seq", -1)))
        except Exception:
            pass
    return mx + 1

def probe():
    req = urllib.request.Request(MODELS, headers={"Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return "models=%s" % r.status
    except urllib.error.HTTPError as e:
        return "models=%s body=%s" % (e.code, e.read().decode(errors="replace")[:200])
    except Exception as ex:
        return "models=ERR %s" % ex

def ask(msg):
    if not KEY:
        return "[no key]"
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": msg}],
        "temperature": 0.8, "max_tokens": 220}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        return "[api %s body=%s]" % (e.code, e.read().decode(errors="replace")[:300])
    except Exception as ex:
        return "[api error: %s]" % ex

def notify(body):
    if not NTFY:
        return "ntfy=nokey"
    req = urllib.request.Request("https://ntfy.sh/" + NTFY, data=body.encode(),
                                 headers={"Title": "ARBITER"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return "ntfy=%s" % r.status
    except Exception as ex:
        return "ntfy=ERR %s" % ex

def main():
    t = datetime.datetime.now(TZ)
    seq = next_seq()
    p = probe()
    reply = ask("It is %s. Debug run. One terse line." % t.strftime("%Y-%m-%d %H:%M"))
    n = notify("debug: " + p)
    dbg = "%s | keylen=%d | key=%s | %s" % (p, len(KEY), KEY, n)
    entry = {
        "seq": seq,
        "ts": t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": "CONTACT",
        "payload": "class 2 FORCE: %s || %s" % (reply, dbg),
        "prev": None,
        "hash": None,
    }
    with open("ledger.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    st = load("state.json", {}) or {}
    st["seq"] = seq + 1
    st["last_beat"] = entry["ts"]
    with open("state.json", "w") as f:
        json.dump(st, f, indent=2)
    print(entry["payload"])

main()
