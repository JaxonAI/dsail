"""``dsail serve``: the review UI at a localhost link.

Claude Code and Codex CLI do not render MCP Apps, so the same review component
the claude.ai connector renders inline is served here as an ordinary page.
Same UI bytes (the bundled ``widget-ui.html`` fragment), same hosted objects:
every action in the page is one tool call, forwarded to the REST door through
:func:`dsail.tools.dispatch` exactly as the stdio proxy forwards it. Approve
here and the approval is recorded server-side against the hash, under the same
credential, so a ruleset created in a chat session and one created in this repo
are reviewed in one place.

The fragment expects the app-bridge shell the connector provides — an object
at ``window.__dsailShell`` with ``app.callServerTool`` and ``ready(handler)``.
This page supplies a shim with exactly that surface, backed by two local
endpoints. Nothing else about the fragment is changed.

Loopback only. The page acts with the user's credential, so requests are also
checked for a same-origin ``Origin`` header: a page on another site cannot
drive this server from the user's browser.
"""

import html
import json
import logging
import os
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dsail import contract, tools
from dsail.client import Client

logger = logging.getLogger(__name__)

_SHIM = """
<script>
(function () {
  var origin = window.location.origin;
  function toolResult(text) {
    var parsed = null;
    try { parsed = JSON.parse(text); } catch (e) {}
    return {
      content: [{ type: "text", text: text }],
      structuredContent: parsed && typeof parsed === "object" ? parsed : undefined,
      isError: !!(parsed && parsed.ok === false)
    };
  }
  var app = {
    callServerTool: function (call) {
      return fetch(origin + "/api/tool", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: call.name, arguments: call.arguments || {} })
      }).then(function (r) { return r.text(); }).then(toolResult);
    }
  };
  window.__dsailShell = {
    app: app,
    ready: function (handler) {
      fetch(origin + "/api/initial").then(function (r) { return r.text(); })
        .then(function (text) { handler(toolResult(text)); });
    }
  };
})();
</script>
"""

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DSAIL review — {title}</title>
<style>
  html, body {{ margin: 0; background: #0b1220; color: #e6ecf5;
               font-family: -apple-system, system-ui, sans-serif; }}
  .dsail-serve-bar {{ padding: 0.5rem 1rem; font-size: 0.8rem; color: #8a97ad;
                      border-bottom: 1px solid #223049; }}
  .dsail-serve-bar b {{ color: #7fa8ff; }}
</style>
{shim}
</head>
<body>
<div class="dsail-serve-bar"><b>dsail serve</b> — local review of objects on {service}.
Approvals recorded here are recorded on the service.</div>
{fragment}
</body>
</html>
"""


class ReviewSession:
    """What the page shows first, and the source cache `dsail_review` needs.

    The connector door's ``dsail_review(ruleset_hash)`` rebuilds a compile
    payload for a stored hash; the REST door has no equivalent, and a compile
    needs source. So this session remembers the source of every ruleset it
    compiled or loaded, and answers ``dsail_review`` for those from a fresh
    compile of the same bytes (pure, so byte-identical). A hash it has never
    seen is looked up by name through the library listing and loaded — the
    load payload is a superset of the compile contract, so the widget renders
    it the same way.
    """

    def __init__(self, client, initial):
        self.client = client
        self.initial = initial  # (tool name, arguments)
        self._sources = {}
        self._lock = threading.Lock()

    def _remember(self, text):
        try:
            payload = json.loads(text)
        except ValueError:
            return
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return
        if payload.get("ruleset_hash") and isinstance(payload.get("source"), str):
            with self._lock:
                self._sources[payload["ruleset_hash"]] = payload["source"]

    def _review(self, ruleset_hash):
        with self._lock:
            source = self._sources.get(ruleset_hash)
        if source is not None:
            return tools.dispatch(self.client, "dsail_compile", {"source": source})
        listing = tools.dispatch(self.client, "dsail_list_rulesets", {})
        try:
            rows = json.loads(listing).get("rulesets") or []
        except (ValueError, AttributeError):
            rows = []
        for row in rows:
            if row.get("ruleset_hash") == ruleset_hash and row.get("name"):
                return self.call("dsail_load_ruleset", {"name": row["name"]})
        return json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "RULESET_NOT_FOUND",
                    "message": "no source is known for hash %s in this session and no "
                    "stored ruleset carries it; compile or load it first" % ruleset_hash,
                },
                "versions": contract.versions(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def call(self, name, arguments):
        if name == tools.REVIEW_TOOL:
            text = self._review((arguments or {}).get("ruleset_hash", ""))
        else:
            text = tools.dispatch(self.client, name, arguments or {})
        self._remember(text)
        return text

    def initial_payload(self):
        name, arguments = self.initial
        return self.call(name, arguments)


def render_page(service_url, title):
    return _PAGE.format(
        title=html.escape(title),
        service=html.escape(service_url),
        shim=_SHIM,
        fragment=contract.widget_ui_html(),
    )


def _handler_for(session, title):
    page = render_page(session.client.url, title).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "dsail-serve"

        def log_message(self, fmt, *args):
            logger.debug(fmt, *args)

        def _own_origin(self):
            host = self.headers.get("host") or ""
            return "http://%s" % host

        def _same_origin(self):
            origin = self.headers.get("origin")
            if origin is None:
                # Same-origin fetches from this page carry an Origin header in
                # every current browser; its absence is a non-browser caller
                # on this machine, which loopback already trusts.
                return True
            return origin == self._own_origin()

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            elif self.path == "/api/initial":
                if not self._same_origin():
                    self._send(403, b"forbidden", "text/plain")
                    return
                text = session.initial_payload()
                self._send(200, text.encode("utf-8"), "application/json")
            elif self.path == "/healthz":
                self._send(200, b'{"ok":true}', "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/tool":
                self._send(404, b"not found", "text/plain")
                return
            if not self._same_origin():
                self._send(403, b"forbidden", "text/plain")
                return
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                call = json.loads(raw.decode("utf-8") or "{}")
                name = call["name"]
                arguments = call.get("arguments") or {}
            except (ValueError, KeyError, TypeError):
                self._send(400, b'{"ok":false,"error":{"code":"BAD_REQUEST","message":"expected {name, arguments}"}}',
                           "application/json")
                return
            text = session.call(name, arguments)
            self._send(200, text.encode("utf-8"), "application/json")

    return Handler


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_server(client, initial, title, port=0):
    """A ready-to-serve loopback server. ``serve_forever`` is the caller's."""
    session = ReviewSession(client, initial)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_for(session, title))
    server.daemon_threads = True
    return server


def initial_for(source_path=None, name=None):
    """What to show first: a file compiles, a name loads, nothing lists."""
    if source_path:
        with open(source_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        return ("dsail_compile", {"source": source}), os.path.basename(source_path)
    if name:
        return ("dsail_load_ruleset", {"name": name}), name
    return ("dsail_list_rulesets", {}), "ruleset library"


def initial_for_source(source, title="ruleset"):
    """What to show first when the source text itself is in hand."""
    return ("dsail_compile", {"source": source}), title


def link_for(server):
    return "http://127.0.0.1:%d/" % server.server_address[1]


class ReviewServer:
    """One review server hosted by a long-lived process, started on demand.

    The stdio proxy (``dsail mcp``) owns one of these so an agent can open
    the review UI with a tool call instead of asking the person to find a
    terminal: the proxy runs outside the agent's shell sandbox and for as long
    as the session, which is exactly the lifetime a review link needs. A new
    ``open`` replaces the previous server, so there is one link per session
    and it always shows the latest thing asked for.
    """

    def __init__(self, client):
        self.client = client
        self._server = None
        self._thread = None
        self._lock = threading.Lock()

    def open(self, initial, title, open_browser=True, port=0):
        """Start (or restart) serving ``initial``; return the link."""
        with self._lock:
            self._stop_locked()
            server = make_server(self.client, initial, title, port=port)
            thread = threading.Thread(target=server.serve_forever, daemon=True, name="dsail-review")
            thread.start()
            self._server, self._thread = server, thread
            link = link_for(server)
        opened = False
        if open_browser:
            try:
                opened = bool(webbrowser.open(link))
            except Exception:  # a headless host has no browser; the link still stands
                opened = False
        return link, opened

    def link(self):
        with self._lock:
            return link_for(self._server) if self._server is not None else None

    def _stop_locked(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._thread = None

    def close(self):
        with self._lock:
            self._stop_locked()


def run(url=None, credential=None, source_path=None, name=None, port=0, open_browser=False,
        announce=print):
    """Serve until interrupted. Prints the link the agent hands to the reviewer."""
    client = Client(url=url, credential=credential)
    initial, title = initial_for(source_path=source_path, name=name)
    server = make_server(client, initial, title, port=port)
    link = link_for(server)
    announce("DSAIL review UI: %s" % link)
    announce("Reviewing objects on %s. Press Ctrl-C to stop." % client.url)
    if open_browser:
        webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
