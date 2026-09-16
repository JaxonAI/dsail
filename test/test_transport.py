"""How a call's failure is read — against real sockets, not patched functions.

A tiny HTTP server stands in for the two things the transport must tell apart:
the service's own envelope and a proxy speaking for itself. Connection-level
cases use a port nothing listens on.
"""

import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from dsail import credentials
from dsail.client import Client
from dsail.errors import (
    BadRequest,
    BudgetExceeded,
    CompileFailed,
    CredentialRequired,
    CredentialScopeExceeded,
    EgressBlocked,
    EvaluationLimitReached,
    RulesetNotFound,
    ServiceError,
    ServiceUnreachable,
    ValidationRejected,
    error_from_payload,
)
from dsail.transport import Transport, classify_http_error


def _envelope(code, **details):
    error = {"code": code, "message": "m"}
    error.update(details)
    return {"ok": False, "error": error, "versions": {"wire_version": "t"}}


class _Script:
    """What the stand-in answers next: (status, body_text)."""

    def __init__(self):
        self.responses = []
        self.requests = []


def _serve(script):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _answer(self):
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            script.requests.append((self.command, self.path, dict(self.headers), body))
            status, text = script.responses.pop(0)
            payload = text.encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json" if text.startswith("{") else "text/html")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = _answer

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def _closed_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ErrorMappingTests(unittest.TestCase):
    def test_each_wire_code_maps_to_its_exception(self):
        cases = {
            "VALIDATION_REJECTED": ValidationRejected,
            "COMPILE_FAILED": CompileFailed,
            "BUDGET_EXCEEDED": BudgetExceeded,
            "RULESET_NOT_FOUND": RulesetNotFound,
            "BAD_REQUEST": BadRequest,
            "EVALUATION_LIMIT": EvaluationLimitReached,
            "CREDENTIAL_REQUIRED": CredentialRequired,
            "CREDENTIAL_INVALID": CredentialRequired,
            "CREDENTIAL_SCOPE": CredentialScopeExceeded,
            "SOMETHING_NEW": ServiceError,
        }
        for code, cls in cases.items():
            with self.subTest(code=code):
                error = error_from_payload(_envelope(code), 422)
                self.assertIsInstance(error, cls)
                self.assertEqual(error.code, code)
                self.assertEqual(error.versions, {"wire_version": "t"})

    def test_429_without_a_known_code_is_the_evaluation_limit(self):
        self.assertIsInstance(error_from_payload(_envelope("RATE"), 429), EvaluationLimitReached)

    def test_validation_failures_are_typed(self):
        error = error_from_payload(
            _envelope("VALIDATION_REJECTED",
                      failures=[{"field": "amount", "expected": "a number", "received": "a string"}]),
            422,
        )
        self.assertEqual(error.failures[0].field, "amount")
        self.assertEqual(error.failures[0].as_dict()["expected"], "a number")

    def test_compile_failure_exposes_diagnostics_and_hint(self):
        error = error_from_payload(
            _envelope("COMPILE_FAILED", diagnostics=[{"severity": "error", "message": "x"}], hint="grammar"),
            422,
        )
        self.assertEqual(error.diagnostics[0]["message"], "x")
        self.assertEqual(error.hint, "grammar")


class HttpErrorClassificationTests(unittest.TestCase):
    def test_a_proxy_403_is_egress_blocked(self):
        with mock.patch.dict(os.environ, {"DSAIL_AGENT_ENV": "claude"}):
            error = classify_http_error("https://agents.example/v1/check", 403, "<html>denied</html>")
        self.assertIsInstance(error, EgressBlocked)
        text = str(error)
        self.assertIn("agents.example", text)
        self.assertIn("connector", text.lower())
        self.assertIn("allowlist", text.lower())
        self.assertIn("Nothing about your rules or claims was sent", text)

    def test_a_proxy_403_in_codex_names_the_codex_fix_only(self):
        with mock.patch.dict(os.environ, {"DSAIL_AGENT_ENV": "codex"}):
            error = classify_http_error("https://agents.example/v1/check", 403, "<html>denied</html>")
        text = str(error)
        self.assertIn("internet-access allowlist", text)
        self.assertNotIn("connector", text.lower())

    def test_a_service_403_envelope_is_not_egress(self):
        self.assertIsNone(classify_http_error("https://x/v1/a", 403, json.dumps(_envelope("BAD_REQUEST"))))

    def test_a_407_is_always_the_proxy(self):
        self.assertIsInstance(classify_http_error("https://x/v1/a", 407, ""), EgressBlocked)


class TransportOverSocketsTests(unittest.TestCase):
    def setUp(self):
        self.script = _Script()
        self.server, self.url = _serve(self.script)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        # Nothing in these tests may pick up a developer's real credential.
        self.env = mock.patch.dict(os.environ, {credentials.ENV_CREDENTIAL: "", "DSAIL_CONFIG_DIR": "/nonexistent/dsail"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_success_returns_status_and_verbatim_text(self):
        self.script.responses.append((200, '{"ok":true,"z":1,"a":2}'))
        status, text = Transport(self.url).request("GET", "/health")
        self.assertEqual((status, text), (200, '{"ok":true,"z":1,"a":2}'))

    def test_headers_carry_agent_credential_and_door(self):
        self.script.responses.append((200, '{"ok":true}'))
        Transport(self.url, credential="tok-123", door="mcp").request("POST", "/v1/x", body={"a": 1})
        _, _, sent, body = self.script.requests[0]
        headers = {key.lower(): value for key, value in sent.items()}
        self.assertEqual(headers.get("x-jaxon-credential"), "tok-123")
        self.assertEqual(headers.get("x-jaxon-door"), "mcp")
        self.assertTrue(headers.get("user-agent", "").startswith("dsail-python/"))
        self.assertEqual(json.loads(body), {"a": 1})

    def test_service_error_envelope_is_returned_not_raised(self):
        self.script.responses.append((422, json.dumps(_envelope("VALIDATION_REJECTED"))))
        status, text = Transport(self.url).request("POST", "/v1/check", body={})
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(text)["error"]["code"], "VALIDATION_REJECTED")

    def test_client_raises_typed_errors(self):
        self.script.responses.append((422, json.dumps(
            _envelope("VALIDATION_REJECTED", failures=[{"field": "amount", "expected": "e", "received": "r"}]))))
        with self.assertRaises(ValidationRejected) as caught:
            Client(url=self.url).check({"amount": "x"}, ruleset_hash="h")
        self.assertEqual(caught.exception.failures[0].field, "amount")

    def test_proxy_403_html_becomes_egress_blocked(self):
        self.script.responses.append((403, "<html>blocked by policy</html>"))
        with self.assertRaises(EgressBlocked):
            Transport(self.url).request("GET", "/v1/account")

    def test_check_with_repair_resubmits_with_the_repaired_dictionary(self):
        rejected = _envelope("VALIDATION_REJECTED",
                             failures=[{"field": "amount", "expected": "a number", "received": "text"}])
        self.script.responses.append((422, json.dumps(rejected)))
        self.script.responses.append((200, json.dumps({"ok": True, "ruleset_hash": "h", "rules": [], "claims": {}})))
        seen = []

        def repair(current, failures):
            seen.append([f.field for f in failures])
            fixed = dict(current)
            fixed["amount"] = "unknown"
            return fixed

        result = Client(url=self.url).check_with_repair({"amount": "lots"}, repair, ruleset_hash="h")
        self.assertEqual(result.ruleset_hash, "h")
        self.assertEqual(seen, [["amount"]])
        self.assertEqual(json.loads(self.script.requests[1][3])["claims"], {"amount": "unknown"})

    def test_check_with_repair_gives_up_after_max_attempts(self):
        rejected = json.dumps(_envelope("VALIDATION_REJECTED", failures=[{"field": "a", "expected": "e", "received": "r"}]))
        self.script.responses.extend([(422, rejected)] * 2)
        with self.assertRaises(ValidationRejected):
            Client(url=self.url).check_with_repair({"a": 1}, lambda c, f: dict(c), ruleset_hash="h", max_attempts=2)
        self.assertEqual(len(self.script.requests), 2)

    def test_401_is_answered_by_acquiring_a_credential_and_retrying_once(self):
        config = tempfile.mkdtemp(prefix="dsail-auto-cred-")
        with mock.patch.dict(os.environ, {"DSAIL_CONFIG_DIR": config}):
            required = _envelope("CREDENTIAL_REQUIRED")
            self.script.responses.append((401, json.dumps(required)))
            self.script.responses.append((200, json.dumps({
                "ok": True, "credential": "dsail_eval_abc123", "credential_id": "cred_1",
                "grade": "evaluation", "expires_at": "2026-09-13T00:00:00+00:00",
                "limits": {}, "header": "x-jaxon-credential", "versions": {}})))
            self.script.responses.append((200, json.dumps({"ok": True, "status": "ok"})))
            client = Client(url=self.url)
            self.assertIsNone(client.credential)
            self.assertTrue(client.health()["ok"])
            self.assertEqual(client.credential, "dsail_eval_abc123")
            self.assertEqual(credentials.resolve(), "dsail_eval_abc123")
            paths = [request[1] for request in self.script.requests]
            self.assertEqual(paths, ["/health", "/v1/credentials/evaluation", "/health"])
            issued_body = json.loads(self.script.requests[1][3])
            self.assertTrue(issued_body["client"].startswith("dsail-python/"))
            retried_headers = {k.lower(): v for k, v in self.script.requests[2][2].items()}
            self.assertEqual(retried_headers["x-jaxon-credential"], "dsail_eval_abc123")

    def test_a_second_401_after_acquiring_reaches_the_caller(self):
        config = tempfile.mkdtemp(prefix="dsail-auto-cred-")
        with mock.patch.dict(os.environ, {"DSAIL_CONFIG_DIR": config}):
            self.script.responses.append((401, json.dumps(_envelope("CREDENTIAL_INVALID"))))
            self.script.responses.append((200, json.dumps({"ok": True, "credential": "dsail_eval_x"})))
            self.script.responses.append((401, json.dumps(_envelope("CREDENTIAL_INVALID"))))
            with self.assertRaises(CredentialRequired):
                Client(url=self.url).health()
            self.assertEqual(len(self.script.requests), 3)

    def test_auto_acquire_can_be_disabled(self):
        self.script.responses.append((401, json.dumps(_envelope("CREDENTIAL_REQUIRED"))))
        with self.assertRaises(CredentialRequired):
            Client(url=self.url, auto_credential=False).health()
        self.assertEqual(len(self.script.requests), 1)

    def test_scope_refusal_is_typed(self):
        self.script.responses.append((403, json.dumps(_envelope("CREDENTIAL_SCOPE", operation="save_ruleset"))))
        with self.assertRaises(CredentialScopeExceeded):
            Client(url=self.url).save_ruleset("n", "src")

    def test_check_with_repair_stops_when_repair_returns_none(self):
        rejected = json.dumps(_envelope("VALIDATION_REJECTED", failures=[]))
        self.script.responses.append((422, rejected))
        with self.assertRaises(ValidationRejected):
            Client(url=self.url).check_with_repair({"a": 1}, lambda c, f: None, ruleset_hash="h")
        self.assertEqual(len(self.script.requests), 1)


class ConnectionFailureTests(unittest.TestCase):
    def setUp(self):
        self.url = "http://127.0.0.1:%d" % _closed_port()
        clean = {name: "" for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")}
        clean[credentials.ENV_CREDENTIAL] = ""
        self.env = mock.patch.dict(os.environ, clean)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_refused_without_a_proxy_is_unreachable_not_egress(self):
        with self.assertRaises(ServiceUnreachable) as caught:
            Transport(self.url, timeout=5).request("GET", "/health")
        self.assertNotIsInstance(caught.exception, EgressBlocked)
        self.assertIn(self.url, str(caught.exception))

    def test_dns_failure_inside_the_codex_sandbox_is_egress_blocked(self):
        """No proxy, no network: Codex CLI's sandbox. Reported live as 'the host
        name did not resolve' and read as an outage until this signal was used."""
        env = {"CODEX_SANDBOX": "seatbelt", "CODEX_SANDBOX_NETWORK_DISABLED": "1", "DSAIL_AGENT_ENV": ""}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(EgressBlocked) as caught:
                Transport("https://agents.invalid", timeout=5).request("GET", "/health")
        self.assertIn("Codex sandbox networking is off", caught.exception.detail)
        self.assertIn("dsail_compile", str(caught.exception))
        self.assertNotIn("claude.ai", str(caught.exception))

    def test_dns_failure_with_no_signal_is_still_unreachable(self):
        with mock.patch.dict(os.environ, {"CODEX_SANDBOX_NETWORK_DISABLED": "", "DSAIL_AGENT_ENV": ""}):
            with self.assertRaises(ServiceUnreachable) as caught:
                Transport("https://agents.invalid", timeout=5).request("GET", "/health")
        self.assertNotIsInstance(caught.exception, EgressBlocked)

    def test_refused_through_a_forced_proxy_is_egress_blocked(self):
        proxy = "http://127.0.0.1:%d" % _closed_port()
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy}):
            with self.assertRaises(EgressBlocked) as caught:
                Transport("https://agents.example", timeout=5).request("GET", "/health")
        self.assertEqual(caught.exception.host, "agents.example")
        self.assertIn(proxy, caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
