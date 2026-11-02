"""The offline web app, with a background checker."""
import threading
from datetime import datetime, timedelta

from . import core
from .fieldkit import App, Fail, Raw, card, form, h, line_chart, link, redirect, stats, table

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, target TEXT NOT NULL, option TEXT NOT NULL DEFAULT '', every INTEGER NOT NULL DEFAULT 60, last REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, watch INTEGER NOT NULL REFERENCES watches(id), at TEXT NOT NULL, ok INTEGER NOT NULL, ms REAL NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS samples_watch ON samples (watch, at);
"""


def create_app(db="netwatch.db", clock=datetime.now, runner=core.run_check):
    a = App("NetWatch Lite", db, SCHEMA, [("Status", "/"), ("Add a check", "/add")])

    def history(wid, days=30):
        cut = (clock() - timedelta(days=days)).isoformat()
        return [(datetime.fromisoformat(r["at"]), bool(r["ok"]), r["ms"]) for r in a.rows("SELECT * FROM samples WHERE watch = ? AND at >= ? ORDER BY at", wid, cut)]

    def run_one(w):
        r = runner(w["kind"], w["target"], 5.0, w["option"])
        now = clock()
        a.run("INSERT INTO samples (watch, at, ok, ms, detail) VALUES (?, ?, ?, ?, ?)", w["id"], now.isoformat(timespec="seconds"), int(r.ok), r.ms, r.detail)
        a.run("UPDATE watches SET last = ? WHERE id = ?", now.timestamp(), w["id"])
        return r

    def tick():
        """Run every check that is due. Returns how many ran."""
        n = 0
        now = clock().timestamp()
        for w in a.rows("SELECT * FROM watches"):
            if now - w["last"] >= w["every"]:
                run_one(w)
                n += 1
        a.run("DELETE FROM samples WHERE at < ?", (clock() - timedelta(days=90)).isoformat())
        return n
    a.tick = tick

    def background(stop):
        while not stop.wait(5):
            try:
                with a.lock:
                    tick()
            except Exception as exc:         # a failed round must not end monitoring
                a.log("checker error", exc)
    a.background = background

    @a.route("/")
    def home(req):
        rows = []
        for w in a.rows("SELECT * FROM watches ORDER BY name"):
            s = history(w["id"])
            pairs = [(t, ok) for t, ok, _ in s]
            rows.append({"w": w, "state": core.state(pairs), "up": core.uptime(pairs), "p": core.latency([m for _, ok, m in s if ok])})
        down = sum(r["state"] == "down" for r in rows)
        colours = {"up": "good", "down": "bad", "flaky": "warn", "unknown": "muted"}
        return a.page("Status", f'<meta http-equiv="refresh" content="30">' + stats([("Checks", len(rows)), ("Down now", down)]) + card("Everything we watch", table(rows, [("Check", lambda r: link(f"/watch/{r['w']['id']}", r["w"]["name"])), ("State", lambda r: Raw(f'<span class="{colours[r["state"]]}">{r["state"]}</span>')), ("Uptime, 30 days", lambda r: "" if r["up"] is None else f"{r['up']:.2%}"), ("Typical response", lambda r: "" if r["p"]["p50"] is None or r["w"]["kind"] == "disk" else f"{r['p']['p50']:.0f} ms")], "Add a check to begin.")))

    @a.route("/add", ("GET", "POST"))
    def add(req):
        if req.method == "POST":
            kind = req.text("kind")
            if kind not in ("http", "tcp", "disk"):
                raise Fail("Unknown check")
            a.run("INSERT INTO watches (name, kind, target, option, every) VALUES (?, ?, ?, ?, ?)", req.text("name"), kind, req.text("target"), req.text("option", False), int(req.number("every", 60, 10, 86400)))
            return redirect("/")
        return a.page("Add a check", card("Only watch systems you run or have permission to check", form(a, "/add", [("name", "Name"), ("kind", "Kind", "select", [("http", "Web page"), ("tcp", "TCP port"), ("disk", "Disk space")]), ("target", "Address, host:port, or folder"), ("option", "Text the page must contain, or minimum % free"), ("every", "Check every (seconds)", "number", 60)], "Add")))

    @a.route("/watch/<id>", ("GET", "POST"))
    def watch(req, id):
        w = a.row("SELECT * FROM watches WHERE id = ?", id)
        if w is None:
            raise Fail("No such check", 404)
        if req.method == "POST":
            run_one(w)
            return redirect(f"/watch/{id}")
        s = history(id)
        pairs = [(t, ok) for t, ok, _ in s]
        now = clock()
        out = core.outages(pairs)
        lat = core.latency([m for _, ok, m in s if ok])
        body = stats([("State", core.state(pairs)), ("Outages", len(out)), ("p95 response", "n/a" if lat["p95"] is None else f"{lat['p95']:.0f} ms")])
        body += form(a, f"/watch/{id}", [], "Check now")
        body += card("Response time", line_chart([(t.strftime("%d %b %H:%M"), m) for t, ok, m in s[-200:] if ok]))
        body += card("Outages", table(list(reversed(out)), [("Started", lambda o: o["start"].strftime("%Y-%m-%d %H:%M")), ("Ended", lambda o: o["end"].strftime("%Y-%m-%d %H:%M") if o["end"] else "still down"), ("Lasted", lambda o: core.fmt_duration((o["end"] or now) - o["start"]))], "No outages in the last 30 days."))
        last = a.row("SELECT * FROM samples WHERE watch = ? ORDER BY id DESC LIMIT 1", id)
        if last:
            body += card("Last result", f"<p>{h(last['at'])}: {h(last['detail'])}</p>")
        return a.page(w["name"], body)

    return a


def run(db="netwatch.db", port=8050):
    app = create_app(db)
    stop = threading.Event()
    threading.Thread(target=app.background, args=(stop,), daemon=True).start()
    app.serve(port)
    stop.set()
