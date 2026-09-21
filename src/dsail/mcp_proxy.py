"""``dsail mcp``: the MCP door over stdio, forwarding to the hosted service.

A coding agent registers this command in its own configuration (``dsail init``
writes the entry). The agent owns the process, so there is no port to bind and
nothing left running between sessions; every tool call becomes one HTTPS
request to the REST door and the body comes back verbatim.

The tool list — names, descriptions, input schemas — is the bundled copy of
what the service's own stdio bridge publishes, served through the low-level
server so the schemas go out exactly as generated rather than re-derived from
a Python signature. A model cannot tell which of the two it is talking to,
with one deliberate exception: ``dsail_open_review`` exists only here, because
only a process on the user's machine can start the review UI and open their
browser. Coding agents' shells are sandboxed (Codex CLI's has no network at
all) and their sessions do not render MCP Apps, so this proxy — which the agent
runs outside the sandbox, for the life of the session — is the one place a
review link can be hosted without asking the person to find a terminal.

stdout is the protocol channel. Nothing here may print to it.
"""

import json
import logging
import os
import sys

from dsail import contract, serve, tools
from dsail.client import Client

logger = logging.getLogger(__name__)

#: Appended to the bundled server instructions: how review works when the
#: client renders no widget. The bundled text is the service's; this paragraph
#: is about a tool the service does not have.
LOCAL_INSTRUCTIONS = """

REVIEW IN THIS CLIENT. This client does not render the review widget. When a
ruleset is ready for a person to review or approve, call `dsail_open_review`
(with the exact source you compiled, or its file path, or a stored name): it
starts the review UI on this machine, opens the user's browser, and returns
the link. Repeat that link to the user. Never tell the user a review panel is
open unless `dsail_open_review` returned a link, and do not run `dsail serve`
from a shell yourself — a sandboxed shell has no network and the server would
die with the command; if the tool is unavailable, give the user the exact
command `dsail serve <file>` to run in their own terminal.
"""


def _configure_logging():
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s dsail.mcp %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def _structured(text):
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def open_review(review_server, arguments):
    """Start the review UI for ``arguments`` and return the envelope text."""
    arguments = arguments or {}
    source, path, name = arguments.get("source"), arguments.get("source_path"), arguments.get("name")
    if source:
        initial, title = serve.initial_for_source(source, arguments.get("title") or "ruleset")
    else:
        initial, title = serve.initial_for(source_path=path, name=name)
        title = arguments.get("title") or title
    link, opened = review_server.open(initial, title, open_browser=arguments.get("open_browser", True))
    return tools.open_review_envelope(link, title, opened)


def build_server(client, review_server=None):
    """A low-level MCP server whose tools call ``client``."""
    import anyio
    import mcp.types as types
    from mcp.server.lowlevel import Server

    bundle = contract.tools()
    review_server = review_server or serve.ReviewServer(client)

    def _definition(item):
        # The bundled definition verbatim: title, annotations and output schema
        # ride along when the bundle carries them, so this proxy publishes what
        # the hosted door publishes (TJP-624). Absent fields stay absent.
        annotations = item.get("annotations")
        return types.Tool(
            name=item["name"],
            title=item.get("title"),
            description=item["description"],
            inputSchema=item["inputSchema"],
            outputSchema=item.get("outputSchema"),
            annotations=types.ToolAnnotations(**annotations) if annotations else None,
        )

    definitions = [_definition(item) for item in tools.proxy_definitions()]

    async def on_list_tools(_context, _params):
        return types.ListToolsResult(tools=definitions)

    async def on_call_tool(_context, params):
        arguments = params.arguments or {}
        if params.name == tools.OPEN_REVIEW_TOOL:
            try:
                text = await anyio.to_thread.run_sync(lambda: open_review(review_server, arguments))
            except OSError as error:
                text = tools.unreachable_envelope(error)
        else:
            # The REST call blocks; keep it off the event loop so a slow solve
            # does not stall the protocol's own pings.
            text = await anyio.to_thread.run_sync(
                lambda: tools.dispatch(client, params.name, arguments)
            )
        structured = _structured(text)
        is_error = bool(structured and structured.get("ok") is False)
        logger.info("tools/call -> %s (%s)", params.name, "error" if is_error else "ok")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structuredContent=structured,
            isError=is_error,
        )

    return Server(
        bundle.get("server_name") or "dsail",
        instructions=contract.instructions() + LOCAL_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def run(url=None, credential=None):
    """Serve over stdio until the client closes the pipe."""
    import anyio
    from mcp.server.stdio import stdio_server

    _configure_logging()
    client = Client(url=url, credential=credential, door="mcp")
    review_server = serve.ReviewServer(client)
    server = build_server(client, review_server)
    logger.info("stdio proxy starting; forwarding to %s (cwd %s)", client.url, os.getcwd())

    async def _main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    try:
        anyio.run(_main)
    finally:
        review_server.close()
