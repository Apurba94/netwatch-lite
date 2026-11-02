import http.server
import os
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

from netwatch_lite import core, create_app
from netwatch_lite.core import Result, check_disk, check_http, check_tcp, fmt_duration, latency, outages, percentile, run_check, state, uptime

T = datetime(2026, 11, 2, 9, 0)


def seq(text):
    """A sample each minute: . for ok, x for failed."""
    return [(T + timedelta(minutes=i), c == ".") for i, c in enumerate(text)]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code = 500 if self.path == "/bad" else 200
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"hello healthy world")

    def log_message(self, *a):
        pass


class TestRealChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.s.server_address[1]
        threading.Thread(target=cls.s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.s.shutdown()
        cls.s.server_close()

    def url(self, p="/"):
        return f"http://127.0.0.1:{self.port}{p}"

    def test_http_ok(self):
        r = check_http(self.url())
        self.assertTrue(r.ok)
        self.assertGreater(r.ms, 0)

    def test_http_failure_status(self):
        r = check_http(self.url("/bad"))
        self.assertFalse(r.ok)
        self.assertIn("500", r.detail)

    def test_http_text(self):
        self.assertTrue(check_http(self.url(), contains="healthy").ok)
        self.assertFalse(check_http(self.url(), contains="missing").ok)

    def test_tcp(self):
        self.assertTrue(check_tcp("127.0.0.1", self.port).ok)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        self.assertFalse(check_tcp("127.0.0.1", free, 1).ok)

    def test_run_check_dispatch(self):
        self.assertTrue(run_check("tcp", f"127.0.0.1:{self.port}").ok)
        self.assertTrue(run_check("http", self.url()).ok)
        self.assertFalse(run_check("tcp", "nonsense").ok)
        self.assertFalse(run_check("ping", "x").ok)

    def test_other_schemes_are_refused(self):
        self.assertFalse(check_http("file:///etc/passwd").ok)

    def test_disk(self):
        d = tempfile.gettempdir()
        self.assertTrue(check_disk(d, 0).ok)
        self.assertFalse(check_disk(d, 101).ok)
        self.assertFalse(check_disk("Z:/does/not/exist/x", 1).ok)


class TestOutages(unittest.TestCase):
    def test_a_blip_is_not_an_outage(self):
        self.assertEqual(outages(seq("..x..x..")), [])

    def test_three_failures_open_one_from_the_first(self):
        o = outages(seq("..xxx.."))
        self.assertEqual(o, [{"start": T + timedelta(minutes=2), "end": T + timedelta(minutes=5)}])

    def test_still_down(self):
        self.assertIsNone(outages(seq("..xxxx"))[0]["end"])

    def test_several(self):
        self.assertEqual(len(outages(seq("xxx.xx.xxxx."))), 2)

    def test_uptime(self):
        self.assertAlmostEqual(uptime(seq("." * 91 + "xxxxx" + "." * 5)), 0.95)
        self.assertEqual(uptime(seq("." * 10)), 1)
        self.assertIsNone(uptime([]))

    def test_uptime_with_gaps_in_checks(self):
        s = [(T, True), (T + timedelta(hours=1), True), (T + timedelta(hours=2), True)]
        self.assertEqual(uptime(s), 1)

    def test_state(self):
        self.assertEqual(state(seq("...")), "up")
        self.assertEqual(state(seq("..x")), "flaky")
        self.assertEqual(state(seq("xxx")), "down")
        self.assertEqual(state([]), "unknown")

    def test_percentiles(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.5), 3)
        self.assertAlmostEqual(percentile([0, 10], 0.95), 9.5)
        self.assertIsNone(latency([])["p50"])

    def test_durations(self):
        self.assertEqual(fmt_duration(timedelta(minutes=5)), "5 min")
        self.assertEqual(fmt_duration(timedelta(minutes=125)), "2 h 5 min")
        self.assertEqual(fmt_duration(timedelta(hours=49)), "2 d 1 h")


class TestApp(unittest.TestCase):
    def setUp(self):
        self.now = [T]
        self.results = {"web": True}

        def fake(kind, target, timeout, option):
            return Result(self.results[target], 12.0, "fake")
        self.a = create_app(":memory:", lambda: self.now[0], fake)
        self.a.post("/add", name="Site", kind="http", target="web", every=60)

    def step(self, ok, n=1):
        for _ in range(n):
            self.results["web"] = ok
            self.now[0] += timedelta(seconds=61)
            self.a.tick()

    def text(self, url="/"):
        return self.a.get(url).body.decode()

    def test_only_due_checks_run(self):
        self.a.tick()
        self.assertEqual(self.a.tick(), 0)
        self.now[0] += timedelta(seconds=61)
        self.assertEqual(self.a.tick(), 1)

    def test_going_down_and_coming_back(self):
        self.step(True, 5)
        self.assertIn(">up<", self.text())
        self.step(False, 3)
        self.assertIn(">down<", self.text())
        self.assertIn("still down", self.text("/watch/1"))
        self.step(True)
        self.assertIn(">up<", self.text())
        self.assertIn("min", self.text("/watch/1").split("Outages")[-1])

    def test_check_now(self):
        self.assertEqual(self.a.post("/watch/1").status, 303)
        self.assertEqual(self.a.one("SELECT COUNT(*) FROM samples"), 1)

    def test_old_history_is_trimmed(self):
        self.step(True)
        self.now[0] += timedelta(days=100)
        self.a.tick()
        self.assertEqual(self.a.one("SELECT COUNT(*) FROM samples"), 1)

    def test_bad_inputs(self):
        self.assertEqual(self.a.post("/add", name="x", kind="ftp", target="a").status, 400)
        self.assertEqual(self.a.post("/add", name="Site", kind="http", target="a").status, 409)
        self.assertEqual(self.a.get("/watch/9").status, 404)


if __name__ == "__main__":
    unittest.main()
