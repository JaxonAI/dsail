"""One HTTP call to the service, and the reading of what came back.

Standard library only, on purpose: this is the whole network surface of the
package and it should be auditable in one screen. Three outcomes:

* the service answered — success or its own error envelope — and the caller
  gets ``(status, text)`` verbatim. Verbatim matters: the service's responses
  are canonical JSON and the stdio proxy returns them byte-for-byte.
* the service was not reached, and the failure looks like a policy proxy
  denying egress: :class:`~dsail.errors.EgressBlocked`, whose text an agent
  relays to a human.
* the service was not reached for any other reason:
  :class:`~dsail.errors.ServiceUnreachable`.

Telling the second from the third is the part that is easy to skip and most
likely to burn a session. The signals, in order of confidence:

1. an HTTP 403 or 407 whose body is NOT the service's envelope — a forward
   proxy speaking for itself. The service never returns 407, and a 403 from
   it would carry ``{"ok": false, "error": ...}``.
2. a connection refused or reset when an HTTP(S) proxy is configured in the
   environment — the proxy is what refused, not the service.
3. a DNS failure for the service host while a proxy is configured — a
   sandbox that resolves nothing but its allowlist.
4. any connection-level failure while Codex has exported
   ``CODEX_SANDBOX_NETWORK_DISABLED=1`` — its shell sandbox has no network
   and no proxy, so DNS fails first and would otherwise read as an outage.

A refused connection with no proxy configured and no such signal is a service
that is down, and is reported as exactly that.
"""

import json
import os
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from dsail import credentials, environment, session
from dsail._version import __version__
from dsail.errors import EgressBlocked, ServiceUnreachable

USER_AGENT = "dsail-python/%s" % __version__
DOOR_HEADER = "x-jaxon-door"

_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def proxy_in_effect():
    """The proxy URL the environment forces, if any."""
    for name in _PROXY_VARS:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _looks_like_envelope(text):
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and "ok" in payload and "error" in payload


def classify_http_error(url, status, text):
    """An :class:`EgressBlocked` for a proxy's own refusal, else ``None``."""
    if status in (403, 407) and not _looks_like_envelope(text):
        host = urllib.parse.urlsplit(url).hostname or url
        detail = "HTTP %d from the network proxy, not from the service" % status
        return EgressBlocked(url, detail, host)
    return None


def classify_url_error(url, error):
    """The right exception for a call that never got an HTTP response."""
    host = urllib.parse.urlsplit(url).hostname or url
    reason = getattr(error, "reason", error)
    detail = str(reason) or type(reason).__name__
    proxied = proxy_in_effect()
    if proxied:
        # Anything short of an HTTP response while a proxy is forced is the
        # proxy speaking: it refused, reset, or never resolved the name.
        return EgressBlocked(url, "%s via proxy %s" % (detail, proxied), host)
    if environment.codex_network_disabled():
        # Codex CLI's sandbox: no proxy, no network at all. DNS fails first,
        # which without this signal reads as the service being down.
        return EgressBlocked(url, "%s; Codex sandbox networking is off" % detail, host)
    if isinstance(reason, ssl.SSLError):
        return ServiceUnreachable(url, "TLS handshake failed: %s" % detail)
    if isinstance(reason, (socket.gaierror, socket.herror)):
        return ServiceUnreachable(url, "the host name did not resolve: %s" % detail)
    return ServiceUnreachable(url, detail)


class Transport:
    """Calls against one base URL, with one credential."""

    def __init__(self, base_url, credential=None, timeout=120, door=None):
        self.base_url = base_url.rstrip("/")
        self.credential = credentials.resolve(credential)
        self.session = session.resolve()
        self.timeout = timeout
        self.door = door

    def url_for(self, path, query=None):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )
        return url

    def request(self, method, path, body=None, query=None):
        """``(status, text)`` for one call. Raises only when nothing answered."""
        url = self.url_for(path, query)
        data = None
        headers = {"user-agent": USER_AGENT, "accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        if self.credential:
            headers[credentials.HEADER] = self.credential
        # Both, when both are held, and that is the point: the session says WHO
        # is calling, the key says which workspace unattended code may reach.
        # The service reads the session for anything about people and the key
        # for everything else, so sending one must not mean dropping the other.
        if self.session:
            headers["authorization"] = "Bearer " + self.session
        if self.door:
            headers[DOOR_HEADER] = self.door
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        # A fresh opener per call, so the proxy configuration is read from the
        # environment NOW. ``urllib.request.urlopen`` builds one global opener
        # on first use and keeps its ProxyHandler for the life of the process,
        # which makes a proxy set at import time bind every later call even
        # after the variable is gone.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", errors="replace")
            blocked = classify_http_error(url, error.code, text)
            if blocked is not None:
                raise blocked from None
            return error.code, text
        except urllib.error.URLError as error:
            raise classify_url_error(url, error) from None
        except socket.timeout as error:
            raise ServiceUnreachable(url, "timed out after %ss" % self.timeout) from error
