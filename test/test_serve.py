"""`dsail serve`: the page carries the widget, the API is same-origin only."""

import json
import threading
import unittest
import urllib.error
import urllib.request

from dsail import serve
from dsail.client import Client

from test import _live


def _get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


def _post(url, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    base = {"content-type": "application/json"}
    base.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=base, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


class _ServerMixin:
    def start(self, client, initial, title="t"):
        self.server = serve.make_server(client, initial, title)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]


class PageTests(_ServerMixin, unittest.TestCase):
    def setUp(self):
        self.start(Client(url="http://127.0.0.1:1", credential=""), ("dsail_list_rulesets", {}))

    def test_page_embeds_the_widget_and_the_shell_shim(self):
        status, page = _get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn("window.__dsailShell", page)
        self.assertIn('id="app"', page)
        self.assertIn("/api/tool", page)
        self.assertIn("shell.ready(", page)
        self.assertEqual(page.count("<!doctype html>"), 1)

    def test_api_refuses_a_foreign_origin(self):
        status, _ = _post(self.base + "/api/tool", {"name": "dsail_list_rulesets"},
                          headers={"origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = _get(self.base + "/api/initial", headers={"origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_api_accepts_its_own_origin_and_relays_unreachable_as_envelope(self):
        status, text = _post(self.base + "/api/tool", {"name": "dsail_list_rulesets", "arguments": {}},
                             headers={"origin": self.base})
        self.assertEqual(status, 200)
        payload = json.loads(text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "HOST_UNREACHABLE")

    def test_malformed_call_is_a_400(self):
        status, _ = _post(self.base + "/api/tool", {"arguments": {}})
        self.assertEqual(status, 400)

    def test_review_of_an_unknown_hash_is_not_found(self):
        session = serve.ReviewSession(Client(url="http://127.0.0.1:1", credential=""), ("dsail_list_rulesets", {}))
        payload = json.loads(session.call("dsail_review", {"ruleset_hash": "f" * 64}))
        self.assertFalse(payload["ok"])
        self.assertIn(payload["error"]["code"], ("RULESET_NOT_FOUND", "HOST_UNREACHABLE"))


class ReviewServerTests(unittest.TestCase):
    def test_open_replaces_the_previous_server_and_close_stops_it(self):
        holder = serve.ReviewServer(Client(url="http://127.0.0.1:1", credential=""))
        self.assertIsNone(holder.link())
        first, opened = holder.open(("dsail_list_rulesets", {}), "a", open_browser=False)
        self.assertFalse(opened)
        self.assertEqual(first, holder.link())
        self.assertEqual(_get(first + "healthz")[0], 200)
        second, _ = holder.open(("dsail_list_rulesets", {}), "b", open_browser=False)
        self.assertEqual(second, holder.link())
        with self.assertRaises(urllib.error.URLError):
            _get(first + "healthz")
        holder.close()
        self.assertIsNone(holder.link())
        with self.assertRaises(urllib.error.URLError):
            _get(second + "healthz")


class LiveReviewTests(_ServerMixin, unittest.TestCase):
    def setUp(self):
        url = _live.live_url()
        if not url:
            self.skipTest(_live.reason())
        self.client = Client(url=url, credential="")
        self.start(self.client, ("dsail_compile", {"source": _live.DEMO_SOURCE}), title="expenses")

    def test_initial_payload_is_the_compile_and_review_reuses_its_source(self):
        status, text = _get(self.base + "/api/initial")
        self.assertEqual(status, 200)
        compiled = json.loads(text)
        self.assertTrue(compiled["ok"], compiled)
        self.assertIn("manifest", compiled)

        # The widget opens a row through dsail_review(hash); the REST door has
        # no such route, so the session answers from the source it just saw.
        status, text = _post(self.base + "/api/tool",
                             {"name": "dsail_review", "arguments": {"ruleset_hash": compiled["ruleset_hash"]}})
        self.assertEqual(status, 200)
        reviewed = json.loads(text)
        self.assertEqual(reviewed["ruleset_hash"], compiled["ruleset_hash"])
        self.assertEqual(reviewed["manifest"], compiled["manifest"])

    def test_check_from_the_page_reaches_the_service(self):
        status, text = _post(self.base + "/api/tool", {
            "name": "dsail_check",
            "arguments": {"source": _live.DEMO_SOURCE,
                          "claims": {"amount": "20 USD", "has_receipt": "unknown", "category": "travel"}},
        })
        self.assertEqual(status, 200)
        payload = json.loads(text)
        self.assertTrue(payload["ok"], payload)
        checks = {a["name"]: a["check"] for r in payload["rules"] for a in r["assertions"]}
        self.assertEqual(checks["receipt_over_75"], "TRUE")
        self.assertEqual(checks["equipment_needs_receipt"], "TRUE")


if __name__ == "__main__":
    unittest.main()
