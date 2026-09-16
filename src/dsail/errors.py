"""Every way a call can fail, as a Python exception a caller can branch on.

Two families:

* :class:`ServiceError` and its subclasses mirror the service's own error
  envelope (``{"ok": false, "error": {"code", "message", ...}}``). One class
  per wire ``code``, each carrying the whole envelope in ``payload`` so nothing
  the service said is lost on the way to the caller.
* :class:`ServiceUnreachable` and :class:`EgressBlocked` describe a call that
  never reached the service. The second is a product surface, not a stack
  trace: an agent that hits it relays ``str(error)`` to a human, and the text
  has to name the one-time fix accurately, for the agent environment the
  process is actually in (:mod:`dsail.environment`). Edit it as carefully as
  UI copy.
"""

from dsail import environment

# ----------------------------------------------------------------------------
# The egress copy. An agent relays this verbatim; keep it accurate and short.
# The fix is different in each agent environment, so the message is assembled
# from a shared head, ONE environment's fix, and a shared footer. Emitting both
# fixes and leaving the person to work out which applies is the failure mode.
# ----------------------------------------------------------------------------

EGRESS_BLOCKED_HEAD = """\
DSAIL could not reach its hosted service at {url}: this environment blocks \
outbound network access from shell commands ({detail}).
"""

EGRESS_FIX_CLAUDE = """\
This is a one-time, organisation-level fix, and either of these resolves it:

  1. Enable the DSAIL connector in claude.ai (Settings -> Connectors). \
Connectors sync into Claude Code sessions automatically, and a Team admin can \
enable it workspace-wide so every developer's session arrives with DSAIL \
present. Connectors are serviced through the platform's own infrastructure \
rather than the sandbox egress path, which is why the connector works where \
this shell call does not.

  2. Or add the DSAIL API domain ({host}) to the workspace network allowlist, \
after which `dsail` and `pip install dsail` work from the shell.
"""

EGRESS_FIX_CODEX = """\
This is a one-time fix in the Codex cloud environment's settings, and it is \
the only path: Codex cloud tasks have no MCP layer, so the DSAIL SDK over \
HTTPS is how every compile and check reaches the service here.

  Add the DSAIL API domain ({host}) to this environment's internet-access \
allowlist: Codex -> Environments -> this environment -> Edit -> Agent internet \
access -> On -> Domain allowlist. Leave the allowed HTTP methods unrestricted \
(a check is a POST). If `pip install dsail` has to run during the task rather \
than in the setup script, allow pypi.org and files.pythonhosted.org as well.
"""

EGRESS_FIX_CODEX_LOCAL = """\
This shell command is running inside Codex CLI's sandbox, which has no network \
access. The `dsail_*` MCP tools do not have this problem: Codex runs MCP \
servers outside the sandbox, so call `dsail_compile` / `dsail_check` instead \
of the `dsail` command. If the tools are not listed (`/mcp`), the server entry \
is missing: run `codex mcp add dsail -- dsail mcp` (an absolute path to `dsail` \
if it is not on PATH) and restart Codex. Do not ask for a network exemption for \
this command; the tools are the path.
"""

EGRESS_FIX_UNKNOWN = """\
This is a one-time fix in the agent environment's network settings, and the \
package could not tell which agent is running (set DSAIL_AGENT_ENV=claude or \
DSAIL_AGENT_ENV=codex to name it). Allow the DSAIL API domain ({host}) in the \
environment's outbound network allowlist; in Claude Code the DSAIL connector in \
claude.ai is an alternative that needs no allowlist change.
"""

EGRESS_BLOCKED_FOOTER = """\
Nothing about your rules or claims was sent: the connection was refused before \
any request left this machine."""

EGRESS_FIXES = {
    environment.CLAUDE: EGRESS_FIX_CLAUDE,
    environment.CODEX: EGRESS_FIX_CODEX,
    environment.CODEX_LOCAL: EGRESS_FIX_CODEX_LOCAL,
    environment.UNKNOWN: EGRESS_FIX_UNKNOWN,
}


def egress_blocked_message(url, detail, host, agent_environment=None):
    """The relay text for ``agent_environment`` (default: the detected one)."""
    which = agent_environment or environment.detect()
    fix = EGRESS_FIXES.get(which, EGRESS_FIX_UNKNOWN)
    return "\n".join((
        EGRESS_BLOCKED_HEAD.format(url=url, detail=detail),
        fix.format(host=host),
        EGRESS_BLOCKED_FOOTER,
    ))


class DSAILError(Exception):
    """Base of every exception this package raises deliberately."""


class ServiceUnreachable(DSAILError):
    """The service did not answer: DNS, connection refused, TLS, timeout.

    Distinct from :class:`EgressBlocked`, which is the same symptom with a known
    cause — a fixed proxy that denies outbound calls — and a known fix.
    """

    def __init__(self, url, detail):
        self.url = url
        self.detail = detail
        super().__init__(
            "the DSAIL service at %s did not answer (%s). Check the URL (DSAIL_URL) "
            "and that this machine can reach it." % (url, detail)
        )


class EgressBlocked(ServiceUnreachable):
    """Outbound access is denied by the environment, not by the service.

    Raised when the failure carries the signature of a policy proxy — an HTTP
    403/407 from something that is not the DSAIL service, or a refused
    connection to a proxy the environment forces every call through — rather
    than a service that is merely down. ``str()`` is the text an agent relays.
    """

    def __init__(self, url, detail, host, agent_environment=None):
        self.host = host
        self.agent_environment = agent_environment or environment.detect()
        DSAILError.__init__(
            self, egress_blocked_message(url, detail, host, self.agent_environment)
        )
        self.url = url
        self.detail = detail


class ServiceError(DSAILError):
    """The service answered with its error envelope.

    ``code`` and ``message`` are the wire fields; ``payload`` is the whole
    envelope, including ``versions``; ``status`` is the HTTP status.
    """

    code = "ENGINE_ERROR"

    def __init__(self, payload, status):
        self.payload = payload
        self.status = status
        error = payload.get("error") or {}
        self.code = error.get("code") or self.code
        self.message = error.get("message") or "the service reported an error"
        self.details = {k: v for k, v in error.items() if k not in ("code", "message")}
        super().__init__("%s: %s" % (self.code, self.message))

    @property
    def versions(self):
        return self.payload.get("versions") or {}


class ValidationRejected(ServiceError):
    """The claim dictionary did not satisfy the ruleset's validation contract.

    ``failures`` is every failing field at once — ``{field, expected,
    received}`` — which is what lets :meth:`Client.check_with_repair` converge
    in one more round trip instead of one per field.
    """

    code = "VALIDATION_REJECTED"

    @property
    def failures(self):
        from dsail.types import Failure

        return [Failure.from_payload(item) for item in self.details.get("failures") or []]


class CompileFailed(ServiceError):
    """The DSAIL source did not compile. ``diagnostics`` and ``hint`` carry why."""

    code = "COMPILE_FAILED"

    @property
    def diagnostics(self):
        return list(self.details.get("diagnostics") or [])

    @property
    def hint(self):
        return self.details.get("hint") or ""


class BudgetExceeded(ServiceError):
    """A per-request complexity budget was exceeded; nothing was solved."""

    code = "BUDGET_EXCEEDED"


class RulesetNotFound(ServiceError):
    """No stored object or name binding for the reference given."""

    code = "RULESET_NOT_FOUND"


class BadRequest(ServiceError):
    """The request body did not match the API contract (shape, not content)."""

    code = "BAD_REQUEST"


class EvaluationLimitReached(ServiceError):
    """This credential's evaluation cap is exhausted.

    The service's message is an upgrade prompt rather than an opaque failure;
    surface it to the user as written.
    """

    code = "EVALUATION_LIMIT"


class CredentialRequired(ServiceError):
    """No usable credential was presented (``CREDENTIAL_REQUIRED`` or ``CREDENTIAL_INVALID``).

    :class:`~dsail.client.Client` normally handles this itself by obtaining an
    evaluation credential and retrying; it reaches the caller only when that
    is disabled or has already been tried once for this call.
    """

    code = "CREDENTIAL_REQUIRED"


class CredentialScopeExceeded(ServiceError):
    """The credential's grade does not allow this operation.

    An evaluation credential compiles and checks; it does not store. The
    message names how to obtain a full credential; show it to the user.
    """

    code = "CREDENTIAL_SCOPE"


CREDENTIAL_CODES = ("CREDENTIAL_REQUIRED", "CREDENTIAL_INVALID")

_BY_CODE = {
    cls.code: cls
    for cls in (
        ValidationRejected,
        CompileFailed,
        BudgetExceeded,
        RulesetNotFound,
        BadRequest,
        EvaluationLimitReached,
        CredentialRequired,
        CredentialScopeExceeded,
    )
}
_BY_CODE["CREDENTIAL_INVALID"] = CredentialRequired


def error_from_payload(payload, status):
    """The right exception for an error envelope.

    Dispatch is on the wire ``code`` first and the HTTP status second, so a
    future code this build has never seen still arrives as a
    :class:`ServiceError` with everything the service said intact.
    """
    code = ((payload.get("error") or {}).get("code")) or ""
    cls = _BY_CODE.get(code)
    if cls is None and status == 429:
        cls = EvaluationLimitReached
    if cls is None:
        cls = ServiceError
    return cls(payload, status)
