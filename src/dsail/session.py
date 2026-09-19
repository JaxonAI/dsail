"""Signing in from a terminal, so the agent's own calls carry a person (TJP-660).

Everything this package did before now is scoped to a *workspace*: compile,
check, save, load, approve. An API key names a workspace, so a key was a
complete answer, and the on-ramp handing one out with no human gate is the whole
reason the first result costs nothing.

Teams are the first thing that is about *people* — invite this person, remove
that one, make them an administrator — and there is nobody inside a key to
attribute that to, nor anybody to hold responsible if it leaks. So those
operations need a signed-in person, and this module is how a terminal gets one.

**It is the service's own OAuth flow, not a second way in.** The service is an
authorization server (its connector door is the reason), its client registration
is open, and Auth0 already sits behind its consent screen. So signing in here is
the same three redirects claude.ai performs, with the browser handed back to a
loopback listener this process opens for the seconds it takes. Nothing is asked
of the identity provider that it does not already do: no new application, no
device-code grant, no callback URL for somebody to register.

**The key is not replaced.** A session token says who you are; the key says
which workspace unattended code may reach. Both are stored, both are sent, and
signing out removes only the first — the programs you have already shipped keep
working, which is the entire point of them having their own credential.
"""

import http.server
import json
import os
import secrets
import socket
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from dsail import credentials

#: Where the session token lives, beside the credential and with the same
#: permissions. A separate file rather than a field in one: they have different
#: lifetimes, and `dsail logout` must be unable to take the key with it.
_FILENAME = "session"

#: What the authorization server is told this client is called. It appears on
#: the approval screen the person is about to read, so it says what is actually
#: asking rather than something they would have to decode.
CLIENT_NAME = "DSAIL command line"

#: How long to wait for the browser round trip before giving up. Generous: it
#: includes reading a consent screen and possibly a fresh sign-in.
TIMEOUT_S = 300

_SUCCESS_PAGE = b"""<!doctype html><meta charset="utf-8">
<title>Signed in</title>
<body style="font:16px system-ui;margin:4rem auto;max-width:28rem">
<h1 style="font-size:1.25rem">Signed in</h1>
<p>You can close this tab and go back to your terminal.</p>
"""

_FAILURE_PAGE = b"""<!doctype html><meta charset="utf-8">
<title>Not signed in</title>
<body style="font:16px system-ui;margin:4rem auto;max-width:28rem">
<h1 style="font-size:1.25rem">Not signed in</h1>
<p>Nothing was changed. Run <code>dsail login</code> again to retry.</p>
"""


class LoginError(Exception):
    """Signing in did not complete. Carries text safe to print."""


def session_path():
    return os.path.join(credentials.config_dir(), _FILENAME)


def resolve():
    """The stored session token, or ``None``. Env override for CI."""
    from_env = os.environ.get("DSAIL_SESSION")
    if from_env and from_env.strip():
        return from_env.strip()
    try:
        with open(session_path(), "r", encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        return None
    return token or None


def store(token):
    directory = credentials.config_dir()
    os.makedirs(directory, exist_ok=True)
    path = session_path()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(token.strip() + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def forget():
    """Sign out. Leaves the API key alone, deliberately."""
    try:
        os.remove(session_path())
    except FileNotFoundError:
        return False
    return True


class _Catcher(http.server.BaseHTTPRequestHandler):
    """Catches the one redirect back, then nothing else ever again."""

    code = None
    state = None
    done = None

    def do_GET(self):  # noqa: N802 - the base class names it
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.code = (query.get("code") or [None])[0]
        _Catcher.state = (query.get("state") or [None])[0]
        body = _SUCCESS_PAGE if _Catcher.code else _FAILURE_PAGE
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _Catcher.done.set()

    def log_message(self, *args):
        """Silence. This server exists for one request and prints nothing."""


def _post(url, payload, form=False):
    if form:
        data = urllib.parse.urlencode(payload).encode("ascii")
        content_type = "application/x-www-form-urlencoded"
    else:
        data = json.dumps(payload).encode("utf-8")
        content_type = "application/json"
    request = urllib.request.Request(
        url, data=data,
        headers={"content-type": content_type, "accept": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler())
    try:
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise LoginError("the service refused this sign-in (%s): %s"
                         % (error.code, detail))
    except (urllib.error.URLError, ValueError, OSError) as error:
        raise LoginError("could not reach the service: %s" % error)


def login(base_url, open_browser=True, printer=None):
    """Sign in, store the session token, and return what the service said.

    ``open_browser`` is False in a session with no browser to open — a remote
    box, or CI — in which case the URL is printed for the person to carry to a
    machine that has one. The loopback listener is still what receives the
    redirect, so that path only works where the browser can reach this host;
    where it cannot, print-and-paste is the honest answer rather than a flow
    that appears to work and then hangs.
    """
    base_url = base_url.rstrip("/")
    say = printer or (lambda text: None)

    # A port the OS picks and we then hand to the server we are about to build:
    # a fixed one collides with whatever else the developer is running, and
    # asking them to choose is a question with no interesting answer.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    redirect_uri = "http://127.0.0.1:%d/callback" % port

    registration = _post(base_url + "/oauth/register", {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    client_id = registration.get("client_id")
    if not client_id:
        raise LoginError("the service registered no client for this sign-in")

    verifier = credentials_verifier()
    state = secrets.token_urlsafe(24)
    authorize_url = base_url + "/authorize?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    })

    _Catcher.code = _Catcher.state = None
    _Catcher.done = threading.Event()
    server = http.server.HTTPServer(("127.0.0.1", port), _Catcher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        say("Opening your browser to sign in. If nothing opens, go to:\n  %s\n"
            % authorize_url)
        if open_browser:
            webbrowser.open(authorize_url)
        if not _Catcher.done.wait(TIMEOUT_S):
            raise LoginError(
                "timed out waiting for the browser. Nothing was changed; run "
                "`dsail login` again to retry.")
    finally:
        server.shutdown()
        server.server_close()

    if not _Catcher.code:
        raise LoginError("the sign-in was declined or did not complete")
    if _Catcher.state != state:
        # The redirect did not come from the authorization we started.
        raise LoginError("this sign-in did not match the request that started it")

    tokens = _post(base_url + "/oauth/token", {
        "grant_type": "authorization_code",
        "code": _Catcher.code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }, form=True)
    access = tokens.get("access_token")
    if not access:
        raise LoginError("the service issued no token for this sign-in")
    store(access)
    return {"stored_at": session_path()}


def credentials_verifier():
    return _b64url(secrets.token_bytes(32))


def _challenge(verifier):
    import hashlib

    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _b64url(raw):
    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
