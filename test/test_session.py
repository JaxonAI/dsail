"""Signing in from a terminal: the flow, its refusals, and what it leaves alone.

The package's other credential is a key, which is issued without a person and
names a workspace. This one is the opposite and the tests are mostly about
keeping the two apart — a session that took the key with it when it went would
break every program the agent had already shipped.
"""

import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from dsail import credentials, session


class _FakeService(BaseHTTPRequestHandler):
    """The two endpoints `dsail login` talks to, and nothing else.

    Registration hands back a client id; the token endpoint hands back an
    access token for a correct code. Both are the service's own — the identity
    provider is behind them and is not part of this flow's surface.
    """

    registered = None
    issued = "tok_from_the_service"

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        if self.path == "/oauth/register":
            _FakeService.registered = json.loads(raw)
            body = json.dumps({"client_id": "client-under-test"}).encode()
        elif self.path == "/oauth/token":
            form = urllib.parse.parse_qs(raw)
            if form.get("code", [None])[0] != "the-code":
                self.send_response(400)
                self.end_headers()
                return
            body = json.dumps({"access_token": _FakeService.issued}).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.config = tempfile.mkdtemp()
        os.environ["DSAIL_CONFIG_DIR"] = self.config
        os.environ.pop("DSAIL_SESSION", None)
        self.service = HTTPServer(("127.0.0.1", 0), _FakeService)
        threading.Thread(target=self.service.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.service.server_port
        _FakeService.registered = None

    def tearDown(self):
        self.service.shutdown()
        self.service.server_close()
        os.environ.pop("DSAIL_CONFIG_DIR", None)
        shutil.rmtree(self.config, ignore_errors=True)

    def _drive_browser(self, code="the-code", use_state=True):
        """Stand in for the person: read the URL, then call the loopback back."""
        def visit(url):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            redirect = query["redirect_uri"][0]
            state = query["state"][0] if use_state else "not-the-state"
            target = urllib.parse.urlparse(redirect)
            connection = http.client.HTTPConnection(target.hostname, target.port)
            connection.request("GET", "%s?code=%s&state=%s"
                               % (target.path, code, urllib.parse.quote(state)))
            connection.getresponse().read()
            connection.close()
        return visit

    def login(self, **kwargs):
        visit = self._drive_browser(**kwargs)
        opened = []

        def opener(url):
            opened.append(url)
            threading.Thread(target=visit, args=(url,), daemon=True).start()
            return True

        import webbrowser

        real = webbrowser.open
        webbrowser.open = opener
        try:
            return session.login(self.url)
        finally:
            webbrowser.open = real

    def test_a_complete_sign_in_stores_a_session(self):
        outcome = self.login()
        self.assertTrue(os.path.exists(outcome["stored_at"]))
        self.assertEqual("tok_from_the_service", session.resolve())

    def test_it_registers_itself_with_a_loopback_redirect(self):
        """Dynamic registration is what makes this need nothing of the provider."""
        self.login()
        registered = _FakeService.registered
        self.assertEqual(session.CLIENT_NAME, registered["client_name"])
        self.assertEqual(1, len(registered["redirect_uris"]))
        self.assertTrue(registered["redirect_uris"][0].startswith("http://127.0.0.1:"))

    def test_the_session_file_is_readable_only_by_its_owner(self):
        outcome = self.login()
        mode = os.stat(outcome["stored_at"]).st_mode & 0o777
        self.assertEqual(0o600, mode)

    def test_a_redirect_carrying_the_wrong_state_is_refused(self):
        """Otherwise a redirect this process did not start could seat a token."""
        with self.assertRaises(session.LoginError) as refused:
            self.login(use_state=False)
        self.assertIn("did not match", str(refused.exception))
        self.assertIsNone(session.resolve())

    def test_a_declined_sign_in_stores_nothing(self):
        with self.assertRaises(session.LoginError):
            self.login(code="")
        self.assertIsNone(session.resolve())

    def test_signing_out_leaves_the_api_key_alone(self):
        """The programs the agent already shipped must keep working."""
        credentials.store("dsail_a_key_for_programs")
        self.login()
        self.assertTrue(session.forget())
        self.assertIsNone(session.resolve())
        self.assertEqual("dsail_a_key_for_programs", credentials.resolve())

    def test_signing_out_twice_is_not_an_error(self):
        self.assertFalse(session.forget())

    def test_the_environment_overrides_the_stored_session(self):
        os.environ["DSAIL_SESSION"] = "tok_from_the_environment"
        try:
            self.login()
            self.assertEqual("tok_from_the_environment", session.resolve())
        finally:
            os.environ.pop("DSAIL_SESSION")


class TransportCarriesBothTests(unittest.TestCase):
    """A session and a key travel together, because they answer different questions."""

    def setUp(self):
        self.config = tempfile.mkdtemp()
        os.environ["DSAIL_CONFIG_DIR"] = self.config

    def tearDown(self):
        os.environ.pop("DSAIL_CONFIG_DIR", None)
        os.environ.pop("DSAIL_SESSION", None)
        shutil.rmtree(self.config, ignore_errors=True)

    def test_both_headers_are_sent_when_both_are_held(self):
        from dsail.transport import Transport

        credentials.store("dsail_a_key")
        session.store("tok_a_session")
        transport = Transport("http://example.test")
        self.assertEqual("dsail_a_key", transport.credential)
        self.assertEqual("tok_a_session", transport.session)

    def test_a_key_alone_still_works(self):
        from dsail.transport import Transport

        credentials.store("dsail_a_key")
        transport = Transport("http://example.test")
        self.assertIsNone(transport.session)
