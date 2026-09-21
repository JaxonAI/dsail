"""`dsail mcp` over a real stdio pipe, driven by the MCP client library.

The tool list is offline and must equal the bundle byte for byte. A call with
no service behind it must come back as the service's own error envelope — a
model reads a cause rather than a dead tool — and, with a live service, a
compile through the proxy must be the same bytes as the same compile over REST.
"""

import asyncio
import json
import os
import socket
import sys
import unittest
import urllib.request

from dsail import contract, tools
from dsail.client import Client

from test import _live


def _closed_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _http_get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=30) as response:
        return response.status, response.read().decode("utf-8")


async def _with_session(url, action):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = dict(os.environ)
    env["DSAIL_URL"] = url
    env["DSAIL_CREDENTIAL"] = ""
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(name, None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "dsail.cli", "mcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await action(session)


def _run(url, action):
    return asyncio.run(_with_session(url, action))


class ToolListTests(unittest.TestCase):
    def test_tool_list_equals_the_bundle(self):
        async def action(session):
            listed = await session.list_tools()
            # The whole definition, the way the bundle records it: title,
            # annotations (wire spelling) and output schema included (TJP-624).
            return [
                {
                    "name": t.name,
                    "title": t.title,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                    "outputSchema": t.output_schema,
                    "annotations": t.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
                    if t.annotations is not None else None,
                }
                for t in listed.tools
            ], session.instructions

        listed, instructions = _run("http://127.0.0.1:%d" % _closed_port(), action)
        self.assertEqual(listed[:-1], contract.tools()["tools"])
        for definition in listed[:-1]:
            with self.subTest(tool=definition["name"]):
                self.assertTrue(definition["title"])
                self.assertIn("readOnlyHint", definition["annotations"])
                self.assertEqual("object", definition["outputSchema"]["type"])
                for name, spec in definition["inputSchema"].get("properties", {}).items():
                    self.assertTrue(spec.get("description"), "%s has no description" % name)
        self.assertEqual(listed[-1]["name"], tools.OPEN_REVIEW_TOOL)
        self.assertTrue(instructions.startswith(contract.instructions()))
        self.assertIn("dsail_open_review", instructions)
        self.assertIn("do not run `dsail serve`", instructions)

    def test_open_review_starts_the_local_ui_and_returns_its_link(self):
        """The ask from the first Codex walk: the agent should open the review
        for the person, not tell them to find a terminal. No service behind it
        here, so the page loads and its first payload is the unreachable
        envelope — the server is up either way."""
        source = "version 1.3;\ndeclare a as boolean;\nassert r { a };\n"

        async def action(session):
            result = await session.call_tool(
                tools.OPEN_REVIEW_TOOL, {"source": source, "open_browser": False, "title": "t"})
            payload = json.loads(result.content[0].text)
            status, page = _http_get(payload["link"])
            status2, initial = _http_get(payload["link"] + "api/initial")
            return result, payload, status, page, status2, initial

        result, payload, status, page, status2, initial = _run("http://127.0.0.1:%d" % _closed_port(), action)
        self.assertFalse(result.is_error)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["link"].startswith("http://127.0.0.1:"))
        self.assertFalse(payload["opened_browser"])
        self.assertIn(payload["link"], payload["message"])
        self.assertEqual(status, 200)
        self.assertIn("window.__dsailShell", page)
        self.assertEqual(status2, 200)
        self.assertEqual(json.loads(initial)["error"]["code"], "HOST_UNREACHABLE")

    def test_call_with_no_service_returns_the_envelope(self):
        async def action(session):
            return await session.call_tool("dsail_get_account_status", {})

        result = _run("http://127.0.0.1:%d" % _closed_port(), action)
        self.assertTrue(result.is_error)
        payload = json.loads(result.content[0].text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "HOST_UNREACHABLE")
        self.assertEqual(result.structured_content, payload)


class LiveParityTests(unittest.TestCase):
    def setUp(self):
        self.url = _live.live_url()
        if not self.url:
            self.skipTest(_live.reason())

    def test_compile_through_the_proxy_is_byte_identical_to_rest(self):
        async def action(session):
            return await session.call_tool("dsail_compile", {"source": _live.DEMO_SOURCE})

        result = _run(self.url, action)
        self.assertFalse(result.is_error, result.content[0].text)
        _status, rest_text = Client(url=self.url, credential="").call(
            "POST", "/v1/compile", body={"source": _live.DEMO_SOURCE, "parent_hash": None, "label": None}
        )
        self.assertEqual(result.content[0].text, rest_text)

    def test_check_through_the_proxy_is_byte_identical_to_rest(self):
        """The parity the Codex story rests on (TJP-622 AC7): Claude Code drives
        the proxy, a Codex cloud task drives REST, and a claim dictionary must
        answer the same bytes through both. Payloads are pure over their
        inputs, so this is equality, not a tolerance."""
        claims = {"amount": "120 USD", "has_receipt": False, "category": "meals"}

        async def action(session):
            return await session.call_tool("dsail_check", {"source": _live.DEMO_SOURCE, "claims": claims})

        result = _run(self.url, action)
        self.assertFalse(result.is_error, result.content[0].text)
        _status, rest_text = Client(url=self.url, credential="").call(
            "POST", "/v1/check",
            body={"claims": claims, "ruleset_hash": None, "source": _live.DEMO_SOURCE, "label": None},
        )
        self.assertEqual(result.content[0].text, rest_text)

    def test_check_through_the_proxy_reports_the_engines_words(self):
        async def action(session):
            return await session.call_tool(
                "dsail_check",
                {"source": _live.DEMO_SOURCE,
                 "claims": {"amount": "120 USD", "has_receipt": False, "category": "meals"}},
            )

        result = _run(self.url, action)
        payload = json.loads(result.content[0].text)
        self.assertTrue(payload["ok"], payload)
        by_name = {a["name"]: a["check"] for r in payload["rules"] for a in r["assertions"]}
        self.assertEqual(by_name["receipt_over_75"], "FALSE")
        self.assertEqual(by_name["within_cap"], "TRUE")
        self.assertNotIn("verdict", payload)


if __name__ == "__main__":
    unittest.main()
