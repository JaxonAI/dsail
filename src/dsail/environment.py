"""Which agent environment this process is running inside, read from its environment.

The one thing that depends on the answer is the egress-blocked message: the
one-time fix for a sandbox that denies outbound calls is different in Claude
Code (enable the connector, or extend the workspace network allowlist) and in
a Codex cloud task (add the domain to the environment's internet-access
allowlist), and a message that lists both makes the person work out which one
applies. So the package detects the host and names one fix.

Detection reads environment variables only, never a user agent or a process
list. Codex exports ``CODEX_SANDBOX`` and ``CODEX_SANDBOX_NETWORK_DISABLED``
to the commands it runs, the Codex cloud base image configures its runtimes
through ``CODEX_ENV_*``, and ``CODEX_HOME`` is Codex's own state root; any
``CODEX_``-prefixed variable is therefore Codex. Claude Code exports
``CLAUDECODE`` to its shell and its cloud sessions carry ``CLAUDE_CODE_*``.
``DSAIL_AGENT_ENV`` overrides both, for a host neither signature fits.
"""

import os

CODEX = "codex"
CLAUDE = "claude"
UNKNOWN = "unknown"

KNOWN = (CODEX, CLAUDE)

#: The explicit override. ``codex`` or ``claude``; anything else is ignored.
ENV_OVERRIDE = "DSAIL_AGENT_ENV"

_CODEX_PREFIX = "CODEX_"
_CLAUDE_EXACT = ("CLAUDECODE",)
_CLAUDE_PREFIX = "CLAUDE_CODE_"


#: Codex exports this, set to ``1``, to every command it runs with networking
#: off — the default for Codex CLI's shell sandbox. Such a command fails on
#: DNS with no proxy anywhere in sight, which looks exactly like a service
#: outage unless this variable is read.
CODEX_NETWORK_DISABLED = "CODEX_SANDBOX_NETWORK_DISABLED"

#: Codex CLI names its local sandbox here (``seatbelt`` on macOS, ``landlock``
#: on Linux). Its presence distinguishes a local Codex session, where the MCP
#: tools run outside the sandbox and are the way through, from a cloud task,
#: where there is no MCP layer and the environment's allowlist is the fix.
CODEX_SANDBOX = "CODEX_SANDBOX"

CODEX_LOCAL = "codex-local"


def codex_network_disabled(environ=None):
    """Whether Codex has told this process it is running with networking off."""
    environ = os.environ if environ is None else environ
    return (environ.get(CODEX_NETWORK_DISABLED) or "").strip().lower() in ("1", "true", "yes")


def codex_flavor(environ=None):
    """``codex-local`` when Codex CLI's sandbox is named, else ``codex``."""
    environ = os.environ if environ is None else environ
    return CODEX_LOCAL if environ.get(CODEX_SANDBOX) else CODEX


def detect(environ=None):
    """``codex-local``, ``codex``, ``claude`` or ``unknown`` for ``environ`` (default: this process's).

    ``codex-local`` is Codex CLI's own sandbox (``CODEX_SANDBOX`` named);
    ``codex`` is Codex with no local sandbox named — a cloud task.
    """
    environ = os.environ if environ is None else environ
    override = (environ.get(ENV_OVERRIDE) or "").strip().lower()
    if override == CODEX:
        return codex_flavor(environ)
    if override in KNOWN or override == CODEX_LOCAL:
        return override
    names = list(environ)
    if any(name.startswith(_CODEX_PREFIX) for name in names):
        return codex_flavor(environ)
    if any(name in _CLAUDE_EXACT or name.startswith(_CLAUDE_PREFIX) for name in names):
        return CLAUDE
    return UNKNOWN
