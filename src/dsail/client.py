"""The REST client.

One method per route on the service's REST door, each returning a typed view
over the payload (or the raw dictionary where no view adds anything), and one
loop — :meth:`Client.check_with_repair` — for the thing an extraction pipeline
actually needs: submit, read every rejected field at once, let the caller's
model fix them, resubmit.

The client sends what you give it and returns what the service said. It
performs no unit arithmetic, invents no values, and never turns a result into
a verdict.
"""

import json
import os
import urllib.parse

from dsail import credentials
from dsail.errors import CREDENTIAL_CODES, ServiceError, ValidationRejected, error_from_payload
from dsail.transport import USER_AGENT, Transport
from dsail.types import CheckResult, CompileResult, PromptPack

#: The hosted service. Override with ``DSAIL_URL`` or ``Client(url=...)``.
DEFAULT_URL = "https://agents.jaxon.ai"
ENV_URL = "DSAIL_URL"
ISSUE_PATH = "/v1/credentials/evaluation"


def _error_code(text):
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if isinstance(payload, dict):
        return (payload.get("error") or {}).get("code")
    return None


def resolve_url(explicit=None):
    return (explicit or os.environ.get(ENV_URL) or DEFAULT_URL).rstrip("/")


class Client:
    """A DSAIL service at one URL, called with one credential.

    ``url`` defaults to ``DSAIL_URL`` then the hosted service; ``credential``
    resolves per :mod:`dsail.credentials`. ``door`` tags calls for the usage
    breakdown and is set by the stdio proxy; leave it alone otherwise.
    """

    def __init__(self, url=None, credential=None, timeout=120, door=None, auto_credential=True):
        self.url = resolve_url(url)
        self.transport = Transport(self.url, credential=credential, timeout=timeout, door=door)
        self.auto_credential = auto_credential

    @property
    def credential(self):
        return self.transport.credential

    # ------------------------------------------------------------------ raw

    def call(self, method, path, body=None, query=None):
        """``(status, text)`` verbatim. The proxy and the local UI use this.

        This is also where the no-human-gate on-ramp lives: a deployment that
        requires credentials answers a bare call with 401 and the route that
        issues an evaluation credential. The client takes it up, stores what it
        is handed, and retries the call once. An expired or revoked credential
        is replaced the same way. Nothing here asks a person anything.
        """
        status, text = self.transport.request(method, path, body=body, query=query)
        if status == 401 and self.auto_credential and path != ISSUE_PATH:
            if _error_code(text) in CREDENTIAL_CODES:
                self.acquire_evaluation_credential()
                status, text = self.transport.request(method, path, body=body, query=query)
        return status, text

    def acquire_evaluation_credential(self):
        """Obtain an evaluation credential from the service and use it from now on.

        Scoped to compile, check and the prompt pack, capped and expiring —
        the service enforces all of that. Stored per :mod:`dsail.credentials`
        so later processes (the MCP proxy, the review UI) share it. Returns the
        issuance payload minus the token.
        """
        status, text = self.transport.request(
            "POST", ISSUE_PATH, body={"client": USER_AGENT}
        )
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {}
        if status >= 400 or not isinstance(payload, dict) or not payload.get("credential"):
            raise error_from_payload(payload if isinstance(payload, dict) else {}, status)
        self.store_credential(payload["credential"])
        return {key: value for key, value in payload.items() if key != "credential"}

    def _json(self, method, path, body=None, query=None):
        status, text = self.call(method, path, body=body, query=query)
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {
                "ok": False,
                "error": {
                    "code": "ENGINE_ERROR",
                    "message": "the service returned a non-JSON body (HTTP %d)" % status,
                    "body": text[:2000],
                },
                "versions": {},
            }
        if status >= 400 or (isinstance(payload, dict) and payload.get("ok") is False):
            raise error_from_payload(payload if isinstance(payload, dict) else {}, status)
        return payload

    # ------------------------------------------------------------ operations

    def health(self):
        return self._json("GET", "/health")

    def version(self):
        """Which engine and wire contract are answering."""
        return self._json("GET", "/version")

    def account(self):
        """This credential's entitlement and usage position."""
        return self._json("GET", "/v1/account")

    def vocabulary(self):
        return self._json("GET", "/v1/vocabulary")

    # ---------------------------------------------------------------- rulesets

    def compile(self, source, parent_hash=None, label=None):
        """Compile DSAIL source: hash, manifest, review text, claim schema, contract."""
        body = {"source": source, "parent_hash": parent_hash, "label": label}
        return CompileResult(self._json("POST", "/v1/compile", body))

    def check(self, claims, ruleset_hash=None, source=None, label=None):
        """Validate a claim dictionary and solve it, in one call.

        Pass ``ruleset_hash`` (from :meth:`compile`) or ``source``. A claim you
        could not determine is ``"unknown"`` (or ``None``) — never a guess,
        never omitted. Raises :class:`~dsail.errors.ValidationRejected` with
        every failing field when the dictionary does not meet the contract.
        """
        if ruleset_hash is None and source is None:
            raise ValueError("check needs ruleset_hash or source")
        body = {"claims": claims, "ruleset_hash": ruleset_hash, "source": source, "label": label}
        return CheckResult(self._json("POST", "/v1/check", body))

    def check_with_repair(self, claims, repair, ruleset_hash=None, source=None,
                          label=None, max_attempts=3):
        """:meth:`check`, re-submitting after the caller repairs rejected fields.

        ``repair(claims, failures)`` receives the current dictionary and every
        :class:`~dsail.types.Failure` the service named, and returns the
        dictionary to submit next — typically by asking the extracting model
        again for exactly those fields, with the ``expected`` text as guidance.
        Returning ``None`` stops the loop and re-raises the rejection. The
        rejection is re-raised after ``max_attempts`` submissions too, so a
        repair that never converges cannot loop forever.

        The integrity rule applies inside ``repair`` as everywhere: a field you
        still cannot determine is repaired to ``"unknown"``, not to a value that
        satisfies the validator.
        """
        attempts = 0
        current = claims
        while True:
            attempts += 1
            try:
                return self.check(current, ruleset_hash=ruleset_hash, source=source, label=label)
            except ValidationRejected as rejected:
                if attempts >= max_attempts:
                    raise
                repaired = repair(current, rejected.failures)
                if repaired is None:
                    raise
                current = repaired

    def prompt_pack(self, ruleset_hash=None, source=None):
        """One extraction prompt per claim, the claim schema, the validation rules."""
        if ruleset_hash and not source:
            path = "/v1/prompt-pack/%s" % urllib.parse.quote(ruleset_hash, safe="")
            return PromptPack(self._json("GET", path))
        if ruleset_hash is None and source is None:
            raise ValueError("prompt_pack needs ruleset_hash or source")
        body = {"ruleset_hash": ruleset_hash, "source": source}
        return PromptPack(self._json("POST", "/v1/prompt-pack", body))

    def save_ruleset(self, name, source, parent_hash=None, label=None):
        """Store a named, immutable revision (parent-linked)."""
        body = {"name": name, "source": source, "parent_hash": parent_hash, "label": label}
        return self._json("POST", "/v1/rulesets", body)

    def list_rulesets(self):
        return self._json("GET", "/v1/rulesets")

    def load_ruleset(self, name):
        """A stored ruleset with its full contract, revision chain and approvals.

        This is how a stored ruleset is shown. Re-compiling source you are
        holding produces a different object the moment your copy and the
        stored bytes disagree, and an approval follows the stored bytes.
        """
        return self._json("GET", "/v1/rulesets/%s" % urllib.parse.quote(name, safe=""))

    def record_approval(self, ruleset_hash, approver, note=None, name=None, label=None):
        """Bind a human approval to one exact hash; ``name`` saves it first."""
        body = {"ruleset_hash": ruleset_hash, "approver": approver, "note": note,
                "name": name, "label": label}
        return self._json("POST", "/v1/approvals", body)

    # ------------------------------------------------------------------- units

    def unit_library(self):
        return self._json("GET", "/v1/units")

    def unit_bridge(self, from_unit, to_unit):
        """Whether one unit converts to another, and by what factor."""
        return self._json("GET", "/v1/units/bridge",
                          query={"from_unit": from_unit, "to_unit": to_unit})

    def add_unit_converter(self, from_unit, to_unit, factor, offset=0.0, attribution=None):
        """Add a converter. The factor is a policy decision a person supplies."""
        body = {"from_unit": from_unit, "to_unit": to_unit, "factor": factor,
                "offset": offset, "attribution": attribution}
        return self._json("POST", "/v1/units/converters", body)

    # ------------------------------------------------------------------ teams
    #
    # Everything here needs a SIGNED-IN PERSON, not a credential: membership is
    # a fact about people, and a key names a workspace. `dsail login` is what
    # puts one behind these calls; without it the service refuses them and says
    # so, naming the command.

    def workspaces(self):
        """Every workspace you may work in, and which one is in use."""
        return self._json("GET", "/v1/workspaces")

    def use_workspace(self, workspace):
        """Work in a team, or in your own workspace (``personal``)."""
        return self._json("POST", "/v1/workspaces/current", {"workspace": workspace})

    def create_team(self, name):
        """Create a team with a ruleset library of its own. It starts empty."""
        return self._json("POST", "/v1/teams", {"name": name})

    def team_members(self, team_id):
        return self._json("GET", "/v1/teams/%s/members" % team_id)

    def invite_to_team(self, team_id, email):
        """Issue a single-use invitation. The link comes back once."""
        return self._json("POST", "/v1/teams/%s/invitations" % team_id,
                          {"email": email})

    def set_team_role(self, team_id, member, role):
        return self._json("POST", "/v1/teams/%s/members/%s/role" % (team_id, member),
                          {"role": role})

    def remove_from_team(self, team_id, member):
        return self._json("DELETE", "/v1/teams/%s/members/%s" % (team_id, member))

    def copy_ruleset(self, name, destination, source=None, new_name=None):
        """Copy one of your rulesets into another of your workspaces."""
        return self._json("POST", "/v1/rulesets/%s/copies" % name,
                          {"destination": destination, "source": source,
                           "new_name": new_name})

    # ------------------------------------------------------------- credentials

    def store_credential(self, token):
        """Persist ``token`` for later processes and use it from now on."""
        path = credentials.store(token)
        self.transport.credential = token.strip()
        return path


__all__ = ["Client", "DEFAULT_URL", "ENV_URL", "resolve_url", "ServiceError"]
