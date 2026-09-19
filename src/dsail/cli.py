"""The ``dsail`` command.

    dsail mcp                       stdio MCP proxy (register this in your agent)
    dsail serve [FILE | --name N]   review UI at a localhost link
    dsail init [DIR]                write the skills, .mcp.json, .codex/config.toml and stanza
    dsail codex-plugin [DIR]        build the Codex plugin bundle
    dsail plugin-bundle [DIR]       build the plugin bundle for Claude Code AND Codex
    dsail compile FILE              compile; print the payload (or --review)
    dsail prompt-pack (FILE | --hash H)
    dsail check (FILE | --hash H) --claims CLAIMS.json
    dsail version | health | whoami
    dsail openapi                   print the bundled OpenAPI document
    dsail credential set|show|forget

Exit codes: 0 ok; 1 usage; 2 the service refused (its error envelope is
printed); 3 the service could not be reached — and when that is because the
environment blocks egress, the message on stderr is the one to relay to the
user, verbatim.
"""

import argparse
import json
import os
import sys

from dsail import contract, credentials, session
from dsail._version import __version__
from dsail.client import DEFAULT_URL, ENV_URL, Client
from dsail.errors import EgressBlocked, ServiceError, ServiceUnreachable

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2
EXIT_UNREACHABLE = 3


def _print_json(payload):
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _client(args):
    return Client(url=args.url, credential=args.credential)


# ----------------------------------------------------------------- commands


def cmd_version(args):
    out = {"dsail": __version__, "contract": contract.versions(), "url": Client(url=args.url).url}
    if args.remote:
        out["service"] = _client(args).version()
    _print_json(out)
    return EXIT_OK


def cmd_health(args):
    _print_json(_client(args).health())
    return EXIT_OK


def cmd_whoami(args):
    _print_json(_client(args).account())
    return EXIT_OK


def cmd_compile(args):
    result = _client(args).compile(_read(args.file), parent_hash=args.parent_hash)
    if args.review:
        sys.stdout.write(result.review.rstrip("\n") + "\n")
        sys.stdout.write("\nruleset_hash: %s\n" % result.ruleset_hash)
    else:
        _print_json(result.payload)
    return EXIT_OK


def cmd_prompt_pack(args):
    client = _client(args)
    if args.file:
        pack = client.prompt_pack(source=_read(args.file))
    else:
        pack = client.prompt_pack(ruleset_hash=args.hash)
    if args.render:
        for prompt in pack.prompts:
            sys.stdout.write("### %s (%s)\n%s\n\n" % (prompt.claim, prompt.data_type, prompt.render()))
        sys.stdout.write("Integrity rule: %s\n" % pack.integrity_rule)
    else:
        _print_json(pack.payload)
    return EXIT_OK


def cmd_check(args):
    client = _client(args)
    claims = json.loads(_read(args.claims))
    if args.file:
        result = client.check(claims, source=_read(args.file))
    else:
        result = client.check(claims, ruleset_hash=args.hash)
    if args.summary:
        for assertion in result.assertions:
            line = "%-10s %s" % (assertion.check, assertion.name)
            if assertion.counterexample:
                line += "   counterexample: %s" % assertion.counterexample
            sys.stdout.write(line + "\n")
        if result.unbound_claims:
            sys.stdout.write("unbound: %s\n" % ", ".join(result.unbound_claims))
    else:
        _print_json(result.payload)
    return EXIT_OK


def cmd_mcp(args):
    from dsail import mcp_proxy

    mcp_proxy.run(url=args.url, credential=args.credential)
    return EXIT_OK


def cmd_serve(args):
    from dsail import serve

    return serve.run(
        url=args.url,
        credential=args.credential,
        source_path=args.file,
        name=args.name,
        port=args.port,
        open_browser=args.open,
    )


def cmd_init(args):
    from dsail import scaffold

    root = os.path.abspath(args.dir)
    stanza_files = tuple(name for name in scaffold.STANZA_FILES
                         if not (name == "AGENTS.md" and args.no_agents_md)
                         and not (name == "CLAUDE.md" and args.no_claude_md))
    outcomes = scaffold.init(
        root,
        url=args.url or os.environ.get(ENV_URL),
        write_skill=not args.no_skill,
        write_mcp=not args.no_mcp,
        write_codex=not args.no_codex,
        stanza_files=stanza_files,
    )
    _print_outcomes(outcomes)
    sys.stdout.write(
        "\nCommit these files: every later agent session in this repository, for "
        "every teammate, picks up the authoring sequence with no prompting.\n"
    )
    if not args.no_codex:
        sys.stdout.write(
            "\nCodex: register the server once for Codex CLI, the IDE extension and the "
            "ChatGPT desktop app (a project-level config may not be read):\n"
            "    codex mcp add dsail -- %s mcp\n" % _dsail_executable()
        )
    return EXIT_OK


def _dsail_executable():
    """The absolute path to this `dsail`, for a config another program spawns from."""
    candidate = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "dsail")
    if os.path.basename(sys.argv[0]) == "dsail" and os.path.exists(candidate):
        return candidate
    return "dsail"


def cmd_codex_plugin(args):
    from dsail import scaffold

    root = os.path.abspath(args.dir)
    outcomes = scaffold.codex_plugin(root, url=args.url or os.environ.get(ENV_URL), app_id=args.app_id)
    _print_outcomes(outcomes)
    sys.stdout.write(
        "\nInstall: `codex plugin marketplace add %s`, then /plugins in Codex. "
        "Codex CLI, the IDE extension and the ChatGPT desktop app share the MCP "
        "configuration, so the server registers once.\n" % root
    )
    _note_app_id(args)
    return EXIT_OK


def cmd_plugin_bundle(args):
    from dsail import scaffold

    root = os.path.abspath(args.dir)
    outcomes = scaffold.plugin_bundle(root, url=args.url or os.environ.get(ENV_URL), app_id=args.app_id)
    _print_outcomes(outcomes)
    sys.stdout.write(
        "\nClaude Code: `/plugin marketplace add %s` then `/plugin install %s@%s`. "
        "Codex: `codex plugin marketplace add %s`, then /plugins.\n"
        % (root, scaffold.PLUGIN_NAME, scaffold.MARKETPLACE_NAME, root)
    )
    _note_app_id(args)
    return EXIT_OK


def _note_app_id(args):
    if not args.app_id:
        sys.stdout.write(
            "No --app-id given, so no ChatGPT connector is bundled; pass the id OpenAI "
            "assigned the DSAIL app to include it.\n"
        )


def _print_outcomes(outcomes):
    for path, outcome in outcomes.items():
        sys.stdout.write("%-10s %s\n" % (outcome, path))


def cmd_openapi(args):
    if args.path:
        sys.stdout.write(contract.path("openapi.json") + "\n")
    else:
        sys.stdout.write(contract.read_text("openapi.json"))
    return EXIT_OK


def cmd_credential(args):
    if args.action == "set":
        token = args.token
        if token is None:
            token = sys.stdin.readline().strip()
        if not token:
            sys.stderr.write("dsail credential set: no token given\n")
            return EXIT_USAGE
        path = credentials.store(token)
        sys.stdout.write("stored at %s\n" % path)
    elif args.action == "show":
        token = credentials.resolve()
        if token is None:
            sys.stdout.write("no credential configured (calls are unauthenticated)\n")
        else:
            shown = token if args.reveal else token[:4] + "…" + token[-4:] if len(token) > 8 else "****"
            sys.stdout.write("%s\n" % shown)
    else:
        removed = credentials.forget()
        sys.stdout.write("removed\n" if removed else "nothing stored\n")
    return EXIT_OK


def cmd_login(args):
    """Sign in, so calls about PEOPLE — teams, invitations — can be attributed.

    Everything else this package does is scoped to a workspace and needs only a
    credential, which is why the on-ramp hands one out with no human gate. This
    is the first thing that needs to know who you are.
    """
    try:
        outcome = session.login(
            Client(url=args.url).url, open_browser=not args.no_browser,
            printer=sys.stderr.write,
        )
    except session.LoginError as error:
        sys.stderr.write("dsail login: %s\n" % error)
        return EXIT_REFUSED
    sys.stdout.write(
        "signed in; session stored at %s\n"
        "Your API key is untouched — that is what the programs you write keep "
        "using.\n" % outcome["stored_at"]
    )
    return EXIT_OK


def cmd_logout(args):
    removed = session.forget()
    sys.stdout.write(
        "signed out (your API key is untouched)\n" if removed
        else "not signed in\n"
    )
    return EXIT_OK


def cmd_whoareyou(args):
    """Whether this terminal has a person behind it, and what that unlocks."""
    token = session.resolve()
    if token is None:
        sys.stdout.write(
            "not signed in.\n"
            "Calls carry a credential, which names a workspace rather than a "
            "person, so compiling, checking, saving and loading all work — but "
            "teams, invitations and switching workspace need `dsail login`.\n"
        )
        return EXIT_OK
    sys.stdout.write("signed in (session stored at %s)\n" % session.session_path())
    return EXIT_OK


def _team_error(error):
    """Print a service refusal, and the remedy when it named one."""
    sys.stderr.write("dsail: %s\n" % error)
    return EXIT_REFUSED


def cmd_workspaces(args):
    try:
        payload = _client(args).workspaces()
    except ServiceError as error:
        return _team_error(error)
    current = payload.get("current")
    for row in payload.get("workspaces", []):
        marker = "*" if row["workspace"] == current else " "
        label = row["name"] if row["kind"] == "team" else "your own workspace"
        sys.stdout.write("%s %-28s %s%s\n" % (
            marker, label, row["team_id"] or "personal",
            "  (%s)" % row["role"] if row.get("role") else ""))
    if payload.get("signed_in") is False:
        sys.stderr.write("\n%s\n" % payload.get("note", ""))
    return EXIT_OK


def cmd_use(args):
    try:
        chosen = _client(args).use_workspace(args.workspace)
    except ServiceError as error:
        return _team_error(error)
    sys.stdout.write("now working in %s\n" % (chosen.get("name") or args.workspace))
    return EXIT_OK


def cmd_team(args):
    client = _client(args)
    try:
        if args.action == "create":
            payload = client.create_team(args.name)
            sys.stdout.write("created %s (%s)\nIt starts empty — copy a ruleset in "
                             "with `dsail copy <name> --to %s`.\n"
                             % (payload["name"], payload["team_id"], payload["team_id"]))
        elif args.action == "members":
            payload = client.team_members(args.team_id)
            for row in payload["members"]:
                sys.stdout.write("%-40s %s\n" % (row["subject"], row["role"]))
        elif args.action == "invite":
            payload = client.invite_to_team(args.team_id, args.email)
            sys.stdout.write(
                "%s\n\nSend that link to %s. Only they can accept it, by signing "
                "in as that address, and only once.\n"
                % (payload["invitation"], args.email))
        elif args.action == "promote":
            client.set_team_role(args.team_id, args.member, "admin")
            sys.stdout.write("%s can now administer this team\n" % args.member)
        elif args.action == "demote":
            client.set_team_role(args.team_id, args.member, "member")
            sys.stdout.write("%s is now an ordinary member\n" % args.member)
        else:
            payload = client.remove_from_team(args.team_id, args.member)
            sys.stdout.write(
                "removed %s; %d key(s) they issued for this team revoked. "
                "What they saved stays with the team.\n"
                % (payload["removed"], payload["credentials_revoked"]))
    except ServiceError as error:
        return _team_error(error)
    return EXIT_OK


def cmd_copy(args):
    try:
        payload = _client(args).copy_ruleset(
            args.name, args.to, source=args.source, new_name=args.as_name)
    except ServiceError as error:
        return _team_error(error)
    sys.stdout.write(
        "copied %s\nThe original is untouched, and the copy arrives unapproved: "
        "an approval belongs to the workspace that recorded it.\n" % payload["name"])
    return EXIT_OK


# -------------------------------------------------------------------- parser


def build_parser():
    parser = argparse.ArgumentParser(
        prog="dsail",
        description="Thin client for the DSAIL hosted service.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=None,
        help="service URL (default: $%s, else %s)" % (ENV_URL, DEFAULT_URL),
    )
    parser.add_argument(
        "--credential",
        default=None,
        help="credential to send (default: $%s, else the stored one)" % credentials.ENV_CREDENTIAL,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("version", help="package, bundled contract and (with --remote) service versions")
    p.add_argument("--remote", action="store_true", help="also ask the service")
    p.set_defaults(func=cmd_version)

    sub.add_parser("health", help="the service's liveness payload").set_defaults(func=cmd_health)
    sub.add_parser("whoami", help="this credential's account status").set_defaults(func=cmd_whoami)

    p = sub.add_parser("compile", help="compile a DSAIL file")
    p.add_argument("file")
    p.add_argument("--parent-hash", default=None)
    p.add_argument("--review", action="store_true", help="print the human review text, not JSON")
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("prompt-pack", help="the extraction contract for a ruleset")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("file", nargs="?")
    group.add_argument("--hash")
    p.add_argument("--render", action="store_true", help="print one prompt per claim as text")
    p.set_defaults(func=cmd_prompt_pack)

    p = sub.add_parser("check", help="validate and solve a claim dictionary")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("file", nargs="?")
    group.add_argument("--hash")
    p.add_argument("--claims", required=True, help="JSON file: claim name -> value")
    p.add_argument("--summary", action="store_true", help="one line per assertion")
    p.set_defaults(func=cmd_check)

    sub.add_parser("mcp", help="serve the MCP door over stdio (for a coding agent)").set_defaults(func=cmd_mcp)

    p = sub.add_parser("serve", help="the review UI at a localhost link")
    p.add_argument("file", nargs="?", help="a DSAIL file to compile and show")
    p.add_argument("--name", default=None, help="a stored ruleset to show")
    p.add_argument("--port", type=int, default=0, help="loopback port (default: any free one)")
    p.add_argument("--open", action="store_true", help="open the link in a browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("init", help="write the skill, .mcp.json entry and instructions stanza")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--no-skill", action="store_true")
    p.add_argument("--no-mcp", action="store_true")
    p.add_argument("--no-agents-md", action="store_true")
    p.add_argument("--no-claude-md", action="store_true")
    p.add_argument("--no-codex", action="store_true",
                   help="skip the Codex skill copy and .codex/config.toml")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("codex-plugin", help="build the Codex plugin bundle (a marketplace of one plugin)")
    p.add_argument("dir", nargs="?", default="dist/codex-plugin")
    p.add_argument("--app-id", default=None,
                   help="the ChatGPT app id OpenAI assigned the DSAIL connector; bundles it when given")
    p.set_defaults(func=cmd_codex_plugin)

    p = sub.add_parser("plugin-bundle",
                       help="build the plugin bundle for BOTH marketplaces, Claude Code and Codex, "
                            "over one plugin directory (what the public repository's root carries)")
    p.add_argument("dir", nargs="?", default="dist/plugin-bundle")
    p.add_argument("--app-id", default=None,
                   help="the ChatGPT app id OpenAI assigned the DSAIL connector; bundles it when given")
    p.set_defaults(func=cmd_plugin_bundle)

    p = sub.add_parser("openapi", help="the bundled OpenAPI document")
    p.add_argument("--path", action="store_true", help="print the file path instead")
    p.set_defaults(func=cmd_openapi)

    sub.add_parser(
        "workspaces", help="every workspace you may work in"
    ).set_defaults(func=cmd_workspaces)

    p = sub.add_parser("use", help="work in a team, or in your own workspace")
    p.add_argument("workspace", help="a team name or id, or `personal`")
    p.set_defaults(func=cmd_use)

    p = sub.add_parser("copy", help="copy a ruleset into another of your workspaces")
    p.add_argument("name", help="the ruleset to copy")
    p.add_argument("--to", required=True, help="destination: a team, or `personal`")
    p.add_argument("--from", dest="source", default=None,
                   help="source workspace (default: the one in use)")
    p.add_argument("--as", dest="as_name", default=None,
                   help="bind the copy under this name instead")
    p.set_defaults(func=cmd_copy)

    p = sub.add_parser("team", help="create a team, invite, remove, promote")
    team_sub = p.add_subparsers(dest="action", required=True)
    create = team_sub.add_parser("create", help="create a team (it starts empty)")
    create.add_argument("name")
    members = team_sub.add_parser("members", help="who is in a team")
    members.add_argument("team_id")
    invite = team_sub.add_parser("invite", help="invite somebody by email address")
    invite.add_argument("team_id")
    invite.add_argument("email")
    for action, helptext in (("promote", "let a member administer the team"),
                             ("demote", "return an administrator to a member"),
                             ("remove", "remove a member and revoke their keys")):
        one = team_sub.add_parser(action, help=helptext)
        one.add_argument("team_id")
        one.add_argument("member")
    p.set_defaults(func=cmd_team)

    p = sub.add_parser(
        "login",
        help="sign in, so teams and invitations can be attributed to you")
    p.add_argument("--no-browser", action="store_true",
                   help="print the URL instead of opening a browser")
    p.set_defaults(func=cmd_login)

    sub.add_parser(
        "logout", help="sign out (leaves the stored credential alone)"
    ).set_defaults(func=cmd_logout)

    sub.add_parser(
        "signed-in", help="whether this terminal has a person behind it"
    ).set_defaults(func=cmd_whoareyou)

    p = sub.add_parser("credential", help="manage the stored credential")
    p.add_argument("action", choices=("set", "show", "forget"))
    p.add_argument("token", nargs="?", help="for set; read from stdin when omitted")
    p.add_argument("--reveal", action="store_true", help="for show; print the whole token")
    p.set_defaults(func=cmd_credential)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except EgressBlocked as blocked:
        sys.stderr.write(str(blocked) + "\n")
        return EXIT_UNREACHABLE
    except ServiceUnreachable as unreachable:
        sys.stderr.write("dsail: %s\n" % unreachable)
        return EXIT_UNREACHABLE
    except ServiceError as refused:
        sys.stderr.write("dsail: %s\n" % refused)
        sys.stderr.write(json.dumps(refused.payload, indent=2, sort_keys=True) + "\n")
        return EXIT_REFUSED
    except (OSError, ValueError) as error:
        sys.stderr.write("dsail: %s\n" % error)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
