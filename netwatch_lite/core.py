"""Check your own services on a schedule, and keep a history of what happened.

An outage opens after several failures in a row, because one failed check is often a blip, and
closes after a success. Only watch systems you run or have permission to check.
"""
import http.client
import shutil
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit


@dataclass
class Result:
    ok: bool
    ms: float
    detail: str = ""


def check_tcp(host, port, timeout=5.0):
    start = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout):
            return Result(True, (time.perf_counter() - start) * 1000, "connected")
    except OSError as exc:
        return Result(False, (time.perf_counter() - start) * 1000, str(exc)[:200])


def check_http(url, timeout=5.0, expect=(200, 399), contains=None):
    """GET a URL and check the status, and optionally some text in the page."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return Result(False, 0, "not an http or https address")
    start = time.perf_counter()
    try:
        kind = http.client.HTTPSConnection if p.scheme == "https" else http.client.HTTPConnection
        args = {"context": ssl.create_default_context()} if p.scheme == "https" else {}
        c = kind(p.hostname, p.port, timeout=timeout, **args)
        c.request("GET", (p.path or "/") + (f"?{p.query}" if p.query else ""), headers={"User-Agent": "netwatch-lite"})
        r = c.getresponse()
        body = r.read(1_000_000)
        ms = (time.perf_counter() - start) * 1000
        c.close()
    except (OSError, http.client.HTTPException) as exc:
        return Result(False, (time.perf_counter() - start) * 1000, str(exc)[:200])
    if not expect[0] <= r.status <= expect[1]:
        return Result(False, ms, f"status {r.status}")
    if contains and contains.encode() not in body:
        return Result(False, ms, f"the page does not contain {contains!r}")
    return Result(True, ms, f"status {r.status}")


def check_disk(path, min_free_percent=10.0):
    try:
        u = shutil.disk_usage(path)
    except OSError as exc:
        return Result(False, 0, str(exc)[:200])
    free = u.free / u.total * 100
    return Result(free >= min_free_percent, 0, f"{free:.1f}% free")


def run_check(kind, target, timeout=5.0, option=""):
    if kind == "tcp":
        host, _, port = target.rpartition(":")
        if not host or not port.isdigit():
            return Result(False, 0, "use host:port")
        return check_tcp(host, port, timeout)
    if kind == "http":
        return check_http(target, timeout, contains=option or None)
    if kind == "disk":
        return check_disk(target, float(option or 10))
    return Result(False, 0, f"unknown check {kind}")


def outages(samples, fail_after=3):
    """samples: time-ordered (time, ok). Returns outages as dicts. One may still be open."""
    out = []
    bad = 0
    first_bad = None
    open_one = None
    for t, ok in samples:
        if ok:
            if open_one:
                open_one["end"] = t
                out.append(open_one)
                open_one = None
            bad = 0
            first_bad = None
        else:
            bad += 1
            if bad == 1:
                first_bad = t
            if bad == fail_after and not open_one:
                open_one = {"start": first_bad, "end": None}
    if open_one:
        out.append(open_one)
    return out


def uptime(samples, fail_after=3, start=None, end=None):
    """Share of the period not inside an outage, by time, so that gaps in checks do not distort it."""
    if not samples:
        return None
    a = start or samples[0][0]
    b = end or samples[-1][0]
    span = (b - a).total_seconds()
    if span <= 0:
        return 1.0 if samples[-1][1] else 0.0
    down = 0.0
    for o in outages(samples, fail_after):
        s = max(o["start"], a)
        e = min(o["end"] or b, b)
        if e > s:
            down += (e - s).total_seconds()
    return max(0.0, 1 - down / span)


def percentile(values, q):
    if not values:
        return None
    s = sorted(values)
    i = (len(s) - 1) * q
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def latency(values):
    return {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95), "max": max(values) if values else None}


def state(samples, fail_after=3):
    """up, flaky (recent failures, no outage yet), or down."""
    if not samples:
        return "unknown"
    o = outages(samples, fail_after)
    if o and o[-1]["end"] is None:
        return "down"
    return "up" if samples[-1][1] else "flaky"


def fmt_duration(delta):
    m = int(delta.total_seconds() // 60)
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if h < 24 else f"{h // 24} d {h % 24} h"
