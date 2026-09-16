"""The bundled contract: what this build knows about the service without asking.

Everything in this directory is GENERATED from ``services/agent_api`` by
``sdk/dsail/scripts/sync_contract.py`` and must not be edited by hand — the
release script refuses to build from a stale bundle. Files:

``openapi.json``    the published OpenAPI document, with every response schema
``tools.json``      the MCP tool definitions the stdio proxy publishes
``instructions.txt`` the server instructions a connected model reads
``authoring.json``  the authoring sequence, grammar guide and integrity rule
``versions.json``   the engine and wire versions the bundle was generated from
``widget-ui.html``  the review UI fragment ``dsail serve`` renders locally
``phrasing.json``   the agreed problem-language phrasing every surface quotes
                    verbatim (TJP-643): the docs URL, the lead, the trigger
                    phrases, each tool's problem statement
"""

import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))

FILES = (
    "openapi.json",
    "tools.json",
    "instructions.txt",
    "authoring.json",
    "versions.json",
    "widget-ui.html",
    "phrasing.json",
)


def path(name):
    return os.path.join(_DIR, name)


def read_text(name):
    with open(path(name), "r", encoding="utf-8") as handle:
        return handle.read()


def read_json(name):
    return json.loads(read_text(name))


def openapi():
    """The OpenAPI 3 document for the REST door."""
    return read_json("openapi.json")


def schemas():
    """The response schemas by name, out of the OpenAPI document."""
    return (openapi().get("components") or {}).get("schemas") or {}


def tools():
    """The stdio proxy's tool definitions: name, description, inputSchema."""
    return read_json("tools.json")


def instructions():
    return read_text("instructions.txt")


def authoring():
    """``{authoring_sequence, grammar_guide, integrity_rule}``."""
    return read_json("authoring.json")


def versions():
    """The engine and wire versions this bundle was generated against."""
    return read_json("versions.json")


def widget_ui_html():
    return read_text("widget-ui.html")


def phrasing():
    """The agreed phrasing: ``docs_url``, ``lead``, ``trigger_phrases``, ``tools`` and more.

    Everything this package says about DSAIL to an agent — the skill
    description ``dsail init`` writes, the README's opening — quotes these
    strings verbatim, because a paraphrase is a different string to every
    retrieval system that indexes it.
    """
    return read_json("phrasing.json")


def docs_url():
    """The agent-facing docs site, no trailing slash."""
    return phrasing()["docs_url"].rstrip("/")
