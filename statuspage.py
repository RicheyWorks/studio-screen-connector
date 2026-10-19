# SPDX-License-Identifier: MIT
r"""studio-screen-connector / statuspage.py: a static "what are the bots building" page for the studio owner.

Reads files only (no server, no broker, never posts to the team link, never sends input to any app):
  * <art>/STATUS.md and <art>/feed.jsonl     the art-queue board (counts, who is waited on, last events)
  * <link-log>                               the team-link log.jsonl (tail only): who posted what, claims, DONE lines
  * <live>/*.png and <live>/capture-state.json   screenshots written by status-capture.ps1
  * TCP connects to 127.0.0.1 for the bridges (9876 Blender, 9877 Unreal, 9890 team link): up/down only
Writes <live>/index.html (self-contained, auto-refreshing) and <live>/status.json. Standard library only.

    python statuspage.py                      # default BLACKBEARD paths under %USERPROFILE%\projects\bot-team
    python statuspage.py --live DIR --art DIR --link-log FILE [--now ISO] [--no-ports]
"""
import argparse
import datetime
import html
import io
import json
import os
import re
import socket
import sys

HIDE_TYPES = {"lock", "unlock", "ack", "heartbeat", "bot.register"}
FOCUS_WORDS = ("CLAIM", "DONE", "DOING", "STATUS", "SYNCED", "TURN-IN", "HANDOFF", "BLOCKED", "NEED")
SECRET = re.compile(r"(?i)\b(password|passwd|secret|private[_ -]?key|api[_-]?key|token|auth_password|mnemonic|seed phrase)\b\s*[=:]\s*\S+")
HEX_KEY = re.compile(r"\b0x[0-9a-fA-F]{64}\b")
COUNTS_ROW = re.compile(r"^\|\s*([A-Z]+ \d+\s*\|\s*)+")


def redact(text):
    """Keys and passwords never go on a page, even a local one."""
    text = SECRET.sub(lambda m: m.group(1) + "=[redacted]", text or "")
    return HEX_KEY.sub("[redacted-hex]", text)


def parse_art_status(text):
    """STATUS.md -> {"updated", "counts": {"READY": 1381, ...}, "waiting_on", "rows"}"""
    out = {"updated": "", "counts": {}, "waiting_on": "", "rows": None}
    for line in (text or "").splitlines():
        m = re.match(r"\s*Updated\s+(\S+)", line)
        if m and not out["updated"]:
            out["updated"] = m.group(1)
        if line.startswith("|") and re.search(r"[A-Z]+ \d+", line) and not out["counts"]:
            for k, v in re.findall(r"([A-Z]+) (\d+)", line):
                out["counts"][k] = int(v)
        m = re.match(r"\*\*Waiting on:\*\*\s*(.+)", line)
        if m and not out["waiting_on"]:
            out["waiting_on"] = m.group(1).strip()
        m = re.match(r"QUEUE\.md rows:\s*(\d+)", line)
        if m:
            out["rows"] = int(m.group(1))
    return out


def tail_lines(path, max_bytes=600000):
    """Last lines of a big file without reading it all."""
    try:
        size = os.path.getsize(path)
        with io.open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if size > max_bytes and lines else lines


def tail_jsonl(path, max_bytes=600000):
    out = []
    for ln in tail_lines(path, max_bytes):
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def tail_visible(path, want=400, start_bytes=600000, max_bytes=24000000):
    """Newest messages that are not lock/ack noise: widen the tail window until `want` are found (or max_bytes)."""
    size = start_bytes
    while True:
        msgs = tail_jsonl(path, size)
        vis = [m for m in msgs if str(m.get("type", "")).lower() not in HIDE_TYPES]
        if len(vis) >= want or size >= max_bytes:
            return vis[-want * 3:] if len(vis) > want * 3 else vis
        size *= 3

def msg_text(m):
    b = m.get("body")
    if isinstance(b, dict):
        for k in ("text", "note", "change", "message"):
            if isinstance(b.get(k), str):
                return b[k]
        return json.dumps(b, sort_keys=True)[:400]
    return str(b or "")


def parse_ts(s):
    try:
        return datetime.datetime.strptime((s or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def age_text(then, now):
    if not then:
        return "unknown"
    s = int((now - then).total_seconds())
    if s < 0:
        return "just now"
    if s < 90:
        return "%ds ago" % s
    if s < 5400:
        return "%dm ago" % (s // 60)
    if s < 172800:
        return "%.1fh ago" % (s / 3600.0)
    return "%dd ago" % (s // 86400)


def is_focus(m):
    t = str(m.get("type", "")).upper()
    first = msg_text(m).strip().split(" ")[0].upper().strip(":") if msg_text(m).strip() else ""
    return any(t.startswith(w) or first.startswith(w) for w in FOCUS_WORDS)


def summarize_link(msgs, now, limit_feed=25, limit_bots=30):
    shown = [m for m in msgs if str(m.get("type", "")).lower() not in HIDE_TYPES]
    last = {}
    for m in shown:
        last[m.get("from", "?")] = m
    bots = []
    for name, m in last.items():
        ts = parse_ts(m.get("ts"))
        bots.append({"bot": name, "type": m.get("type", ""), "ts": m.get("ts", ""), "ago": age_text(ts, now),
                     "active": bool(ts and (now - ts).total_seconds() < 1800), "text": redact(msg_text(m))[:200], "_t": ts or datetime.datetime.min})
    bots.sort(key=lambda b: b["_t"], reverse=True)
    for b in bots:
        del b["_t"]
    focus = [m for m in shown if is_focus(m)][-limit_feed:]
    feed = [{"id": m.get("id"), "ts": m.get("ts", ""), "from": m.get("from", "?"), "type": m.get("type", ""),
             "text": redact(msg_text(m))[:420]} for m in reversed(focus)]
    return {"bots": bots[:limit_bots], "feed": feed, "last_id": (msgs[-1].get("id") if msgs else None)}


def port_up(port, timeout=0.4):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def read_live(live_dir, now):
    shots = []
    state = {}
    sp = os.path.join(live_dir, "capture-state.json")
    try:
        state = json.load(io.open(sp, encoding="utf-8-sig"))
    except (OSError, ValueError):
        state = {}
    try:
        names = sorted(os.listdir(live_dir))
    except OSError:
        names = []
    for n in names:
        if n.lower().endswith(".png"):
            p = os.path.join(live_dir, n)
            mt = datetime.datetime.fromtimestamp(os.path.getmtime(p))
            key = n[:-4]
            st = state.get(key, {}) if isinstance(state, dict) else {}
            shots.append({"name": n, "key": key, "mtime": mt.strftime("%Y-%m-%d %H:%M:%S"), "ago": age_text(mt, now),
                          "stale": (now - mt).total_seconds() > 900, "method": st.get("method", ""), "title": st.get("title", ""),
                          "message": st.get("message", ""), "ok": st.get("ok", None), "ver": int(mt.timestamp())})
    return shots, state


def gather(art_dir, link_log, live_dir, now=None, ports=True):
    now = now or datetime.datetime.now()
    art = {}
    try:
        art = parse_art_status(io.open(os.path.join(art_dir, "STATUS.md"), encoding="utf-8", errors="replace").read())
    except OSError:
        art = parse_art_status("")
    upd = parse_ts(art.get("updated"))
    art["age"] = age_text(upd, now)
    art["stale"] = bool(upd is None or (now - upd).total_seconds() > 1800)
    events = []
    for e in tail_jsonl(os.path.join(art_dir, "feed.jsonl"), 60000)[-12:]:
        events.append(redact(json.dumps(e, sort_keys=True))[:300])
    link = summarize_link(tail_visible(link_log), now)
    shots, state = read_live(live_dir, now)
    health = {}
    if ports:
        health = {"Blender bridge 9876": port_up(9876), "Unreal bridge 9877": port_up(9877), "Team link 9890": port_up(9890)}
    return {"generated": now.strftime("%Y-%m-%d %H:%M:%S"), "art": art, "art_events": events, "link": link,
            "shots": shots, "health": health}


CSS = """
body{font:14px/1.45 Segoe UI,system-ui,sans-serif;background:#14161a;color:#e6e8eb;margin:0;padding:16px 22px}
h1{font-size:22px;margin:0 0 2px}h2{font-size:15px;margin:22px 0 8px;color:#9fb4ff;text-transform:uppercase;letter-spacing:.06em}
.sub{color:#8b93a1;font-size:12px}.grid{display:flex;flex-wrap:wrap;gap:14px}
.card{background:#1d2026;border:1px solid #2b2f38;border-radius:8px;padding:10px 12px}
.pill{display:inline-block;border-radius:10px;padding:1px 9px;margin:0 6px 4px 0;background:#2b2f38}
.up{background:#16442b;color:#8ff0b5}.down{background:#4a1f1f;color:#ff9b9b}.warn{background:#4a3a14;color:#ffd479}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #2b2f38;padding:4px 8px;text-align:left;vertical-align:top}
th{color:#8b93a1;font-weight:600;font-size:12px}.act{color:#8ff0b5}.old{color:#6f7785}
img.shot{max-width:640px;width:100%;border:1px solid #2b2f38;border-radius:6px;display:block}
.cap{font-size:12px;color:#8b93a1;margin-top:4px}code{background:#2b2f38;padding:0 4px;border-radius:3px}
"""


def e(x):
    return html.escape(str(x), quote=True)


def render(d):
    h = ["<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='30'>",
         "<title>RicheyWorks build view</title><style>%s</style></head><body>" % CSS,
         "<h1>What the bots are building</h1><div class='sub'>Generated %s &middot; refreshes every 30 s &middot; read-only, built from the art board, the team-link log and screen captures</div>" % e(d["generated"])]
    h.append("<h2>Health</h2><div>")
    for k, v in d["health"].items():
        h.append("<span class='pill %s'>%s: %s</span>" % ("up" if v else "down", e(k), "up" if v else "down"))
    if not d["health"]:
        h.append("<span class='pill'>port checks off</span>")
    art = d["art"]
    h.append("<span class='pill %s'>art watcher board: %s</span></div>" % ("warn" if art.get("stale") else "up", e(art.get("age", "unknown"))))
    h.append("<h2>Live views</h2><div class='grid'>")
    if not d["shots"]:
        h.append("<div class='card'>No screenshots yet. Run <code>status-loop.ps1</code> (see README, Build view).</div>")
    for s in d["shots"]:
        cap = "%s &middot; %s%s" % (e(s["mtime"]), e(s["ago"]), " &middot; STALE" if s["stale"] else "")
        extra = " &middot; ".join(x for x in (e(s["method"]), e(s["title"])) if x)
        note = ("<div class='cap'>%s</div>" % e(s["message"])) if s["message"] else ""
        h.append("<div class='card'><b>%s</b><br><img class='shot' src='%s?v=%d' alt='%s'><div class='cap'>%s%s</div>%s</div>"
                 % (e(s["key"]), e(s["name"]), s["ver"], e(s["key"]), cap, (" &middot; " + extra) if extra else "", note))
    h.append("</div><h2>Art queue</h2><div class='card'>")
    for k, v in sorted(art.get("counts", {}).items()):
        h.append("<span class='pill'>%s %d</span>" % (e(k), v))
    h.append("<div class='sub'>updated %s (%s); waiting on: %s</div>" % (e(art.get("updated") or "?"), e(art.get("age")), e(art.get("waiting_on") or "n/a")))
    for ev in d["art_events"][-6:]:
        h.append("<div class='cap'>%s</div>" % e(ev))
    h.append("</div><h2>Who is doing what (last post per bot)</h2><table><tr><th>bot</th><th>last post</th><th>type</th><th>text</th></tr>")
    for b in d["link"]["bots"]:
        h.append("<tr><td class='%s'>%s</td><td>%s<br><span class='sub'>%s</span></td><td>%s</td><td>%s</td></tr>"
                 % ("act" if b["active"] else "old", e(b["bot"]), e(b["ago"]), e(b["ts"]), e(b["type"]), e(b["text"])))
    h.append("</table><h2>Claims, status and DONE lines (newest first)</h2><table><tr><th>time</th><th>bot</th><th>type</th><th>text</th></tr>")
    for m in d["link"]["feed"]:
        h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (e(m["ts"]), e(m["from"]), e(m["type"]), e(m["text"])))
    h.append("</table><p class='sub'>Source of truth: bot-team\\BOARD\\ARCHITECTURE.md. Link log last id: %s.</p></body></html>" % e(d["link"].get("last_id")))
    return "".join(h)


def main(argv=None):
    home = os.path.expanduser("~")
    bt = os.path.join(home, "projects", "bot-team")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--art", default=os.path.join(bt, "art"))
    ap.add_argument("--link-log", default=os.path.join(bt, "link", "log.jsonl"))
    ap.add_argument("--live", default=os.path.join(bt, "BOARD", "live"))
    ap.add_argument("--now", default="", help="ISO time for reproducible output (tests)")
    ap.add_argument("--no-ports", action="store_true")
    a = ap.parse_args(argv)
    now = parse_ts(a.now) if a.now else None
    os.makedirs(a.live, exist_ok=True)
    d = gather(a.art, a.link_log, a.live, now, ports=not a.no_ports)
    tmp = os.path.join(a.live, "index.html.tmp")
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(render(d))
    os.replace(tmp, os.path.join(a.live, "index.html"))
    with io.open(os.path.join(a.live, "status.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    print(json.dumps({"ok": True, "html": os.path.join(a.live, "index.html"), "bots": len(d["link"]["bots"]), "shots": len(d["shots"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

