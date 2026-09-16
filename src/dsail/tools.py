"""The MCP tool surface, mapped onto the REST door.

One table, used by two callers: the stdio proxy (``dsail mcp``) and the local
review UI (``dsail serve``). Each tool name resolves to one REST call whose
body is returned verbatim, so a tool result through either is byte-identical to
the same call made with curl — the property the service's own stdio bridge has,
kept here for the same reason.

Tool names and descriptions come from the bundled ``contract/tools.json``; this
module holds only the routing, which is logic rather than data. A name in the
bundle with no route here is a test failure, and so is the reverse.
"""

import json
import urllib.parse

from dsail import contract
from dsail.errors import ServiceUnreachable

#: The one tool that exists on the connector door only. The local review UI
#: calls it to open a stored ruleset; the proxy does not publish it because a
#: stdio client has nothing to render a widget in.
REVIEW_TOOL = "dsail_review"


def _quoted(value):
    return urllib.parse.quote(str(value), safe="")


def _compile(args):
    return "POST", "/v1/compile", {
        "source": args.get("source"),
        "parent_hash": args.get("parent_hash"),
        "label": args.get("label"),
    }, None


def _check(args):
    return "POST", "/v1/check", {
        "claims": args.get("claims"),
        "ruleset_hash": args.get("ruleset_hash"),
        "source": args.get("source"),
        "label": args.get("label"),
    }, None


def _prompt_pack(args):
    if args.get("ruleset_hash") and not args.get("source"):
        return "GET", "/v1/prompt-pack/%s" % _quoted(args["ruleset_hash"]), None, None
    return "POST", "/v1/prompt-pack", {
        "ruleset_hash": args.get("ruleset_hash"),
        "source": args.get("source"),
    }, None


def _save(args):
    return "POST", "/v1/rulesets", {
        "name": args.get("name"),
        "source": args.get("source"),
        "parent_hash": args.get("parent_hash"),
        "label": args.get("label"),
    }, None


def _load(args):
    return "GET", "/v1/rulesets/%s" % _quoted(args.get("name", "")), None, None


def _list(_args):
    return "GET", "/v1/rulesets", None, None


def _approval(args):
    return "POST", "/v1/approvals", {
        "ruleset_hash": args.get("ruleset_hash"),
        "approver": args.get("approver"),
        "note": args.get("note"),
        "name": args.get("name"),
        "label": args.get("label"),
    }, None


def _account(_args):
    return "GET", "/v1/account", None, None


def _issue_api_key(args):
    return "POST", "/v1/credentials/api-key", {"label": args.get("label")}, None


def _units(args):
    if args.get("from_unit") and args.get("to_unit"):
        return "GET", "/v1/units/bridge", None, {
            "from_unit": args["from_unit"],
            "to_unit": args["to_unit"],
        }
    return "GET", "/v1/units", None, None


def _add_converter(args):
    return "POST", "/v1/units/converters", {
        "from_unit": args.get("from_unit"),
        "to_unit": args.get("to_unit"),
        "factor": args.get("factor"),
        "offset": args.get("offset", 0.0),
        "attribution": args.get("attribution"),
    }, None


#: tool name -> function(arguments) -> (method, path, body, query)
ROUTES = {
    "dsail_compile": _compile,
    "dsail_check": _check,
    "dsail_get_prompt_pack": _prompt_pack,
    "dsail_save_ruleset": _save,
    "dsail_load_ruleset": _load,
    "dsail_list_rulesets": _list,
    "dsail_record_approval": _approval,
    "dsail_get_account_status": _account,
    "dsail_issue_api_key": _issue_api_key,
    "dsail_unit_library": _units,
    "dsail_add_unit_converter": _add_converter,
}


#: The one tool that exists only in the local proxy, because only a process on
#: the user's machine can do what it does: start the review UI and open it in
#: their browser. Not in the bundle (the service has no such tool) and not in
#: ROUTES (it makes no REST call of its own). The stdio proxy publishes it
#: after the bundled ten; ``dsail serve`` is the same thing as a command.
OPEN_REVIEW_TOOL = "dsail_open_review"

OPEN_REVIEW_DEFINITION = {
    "name": OPEN_REVIEW_TOOL,
    "description": (
        "Show a person the rules a program will check, so they can approve exactly those "
        "bytes — here, by starting the local DSAIL review UI and opening it in their browser.\n\n"
        "Call this when a ruleset is ready for human review instead of telling the user to run "
        "`dsail serve` themselves. It starts a loopback server on this machine (outside any "
        "shell sandbox), opens the link in the default browser, and returns the link. Pass ONE "
        "of: `source` (the DSAIL text, byte-for-byte as compiled), `source_path` (a .dsail "
        "file in the repository), or `name` (a stored ruleset); nothing opens the ruleset "
        "library. The page shows the same review component the chat clients render; Approve "
        "there is recorded on the service against the exact hash. Always repeat the returned "
        "link to the user in your reply — the browser may not have opened on a remote or "
        "headless machine — and do not claim a review panel is open unless this tool returned "
        "a link."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "DSAIL source to compile and show."},
            "source_path": {"type": "string", "description": "Path to a .dsail file to compile and show."},
            "name": {"type": "string", "description": "A stored ruleset name to load and show."},
            "title": {"type": "string", "description": "Page title; defaults to the file or ruleset name."},
            "open_browser": {"type": "boolean", "description": "Open the link in the default browser (default true)."},
        },
        "additionalProperties": False,
    },
}


def definitions():
    """The bundled tool definitions, in the order the service publishes them."""
    return contract.tools()["tools"]


def proxy_definitions():
    """What ``dsail mcp`` publishes: the bundled ten, then the local review tool."""
    return definitions() + [OPEN_REVIEW_DEFINITION]


def open_review_envelope(link, title, opened_browser):
    return json.dumps(
        {
            "ok": True,
            "link": link,
            "title": title,
            "opened_browser": opened_browser,
            "message": (
                "The DSAIL review UI is open at %s%s. Tell the user that link; Approve there is "
                "recorded on the service against the exact ruleset hash."
                % (link, "" if opened_browser else " (the browser did not open on this machine; "
                   "the user should open the link themselves)")
            ),
            "versions": contract.versions(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def unreachable_envelope(error):
    """The service's error shape, for a call that never reached it.

    A model reads a cause rather than a dead tool. For :class:`EgressBlocked`
    the message is the relay text itself.
    """
    return json.dumps(
        {
            "ok": False,
            "error": {
                "code": "EGRESS_BLOCKED"
                if type(error).__name__ == "EgressBlocked"
                else "HOST_UNREACHABLE",
                "message": str(error),
            },
            "versions": contract.versions(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def unknown_tool_envelope(name):
    return json.dumps(
        {
            "ok": False,
            "error": {"code": "BAD_REQUEST", "message": "no such tool: %s" % name},
            "versions": contract.versions(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def dispatch(client, name, arguments):
    """Call the REST door for one tool; return the body text verbatim.

    Errors from the service are bodies too — they carry the failure list and
    the grammar hint a model needs — so they are returned rather than raised.
    Only a call that never reached the service becomes an envelope made here.
    """
    route = ROUTES.get(name)
    if route is None:
        return unknown_tool_envelope(name)
    method, path, body, query = route(arguments or {})
    try:
        _status, text = client.call(method, path, body=body, query=query)
    except ServiceUnreachable as error:
        return unreachable_envelope(error)
    return text
