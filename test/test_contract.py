"""The bundled contract is complete, coherent, and matches the routing table."""

import json
import unittest

from dsail import contract, tools


class BundleTests(unittest.TestCase):
    def test_every_bundle_file_is_present_and_non_empty(self):
        for name in contract.FILES:
            with self.subTest(name=name):
                self.assertTrue(contract.read_text(name).strip(), name)

    def test_openapi_publishes_the_rest_door(self):
        spec = contract.openapi()
        paths = set(spec["paths"])
        for required in ("/v1/compile", "/v1/check", "/v1/prompt-pack/{ruleset_hash}",
                         "/v1/rulesets", "/v1/approvals", "/v1/units", "/health", "/version"):
            self.assertIn(required, paths)
        # FastAPI's phantom 422 is stripped by the service; the bundle must
        # carry the service's document, not a regenerated one.
        rendered = json.dumps(spec)
        self.assertNotIn("HTTPValidationError", rendered)

    def test_tool_names_match_the_routing_table_exactly(self):
        names = [item["name"] for item in tools.definitions()]
        self.assertEqual(sorted(names), sorted(tools.ROUTES))
        self.assertEqual(len(names), 10)
        self.assertTrue(all(name.startswith("dsail_") for name in names))
        self.assertNotIn(tools.REVIEW_TOOL, names, "the widget-only tool is not proxied")

    def test_the_proxy_adds_exactly_one_local_tool(self):
        proxied = [item["name"] for item in tools.proxy_definitions()]
        self.assertEqual(proxied[:-1], [item["name"] for item in tools.definitions()])
        self.assertEqual(proxied[-1], tools.OPEN_REVIEW_TOOL)
        self.assertNotIn(tools.OPEN_REVIEW_TOOL, tools.ROUTES, "it makes no REST call of its own")
        definition = tools.OPEN_REVIEW_DEFINITION
        self.assertEqual(definition["inputSchema"]["type"], "object")
        for word in ("browser", "link", "dsail serve"):
            self.assertIn(word, definition["description"])

    def test_every_tool_has_a_description_and_an_object_schema(self):
        for item in tools.definitions():
            with self.subTest(tool=item["name"]):
                self.assertTrue(item["description"].strip())
                self.assertEqual(item["inputSchema"]["type"], "object")

    def test_versions_name_the_wire_contract(self):
        versions = contract.versions()
        self.assertIn("wire_version", versions)
        self.assertIn("engine_version", versions)

    def test_instructions_carry_the_authoring_sequence_and_integrity_rule(self):
        instructions = contract.instructions()
        authoring = contract.authoring()
        self.assertIn(authoring["authoring_sequence"].strip().splitlines()[0], instructions)
        self.assertIn(authoring["integrity_rule"], instructions)
        self.assertIn("dsail_compile", instructions)

    def test_widget_fragment_is_the_shell_consuming_fragment(self):
        fragment = contract.widget_ui_html()
        self.assertIn("window.__dsailShell", fragment)
        self.assertIn("shell.ready(", fragment)
        self.assertNotIn("{{UI_BUILD}}", fragment, "the build placeholder must be substituted")


class RoutingTests(unittest.TestCase):
    def test_routes_quote_path_parameters(self):
        method, path, body, query = tools.ROUTES["dsail_load_ruleset"]({"name": "a/b?c"})
        self.assertEqual((method, body, query), ("GET", None, None))
        self.assertEqual(path, "/v1/rulesets/a%2Fb%3Fc")

    def test_prompt_pack_prefers_the_hash_route(self):
        method, path, body, _ = tools.ROUTES["dsail_get_prompt_pack"]({"ruleset_hash": "ab" * 32})
        self.assertEqual(method, "GET")
        self.assertTrue(path.endswith("ab" * 32))
        self.assertIsNone(body)
        method, path, body, _ = tools.ROUTES["dsail_get_prompt_pack"]({"source": "x"})
        self.assertEqual((method, path), ("POST", "/v1/prompt-pack"))

    def test_unit_library_routes_by_argument_shape(self):
        _, path, _, query = tools.ROUTES["dsail_unit_library"]({})
        self.assertEqual((path, query), ("/v1/units", None))
        _, path, _, query = tools.ROUTES["dsail_unit_library"]({"from_unit": "m", "to_unit": "km"})
        self.assertEqual(path, "/v1/units/bridge")
        self.assertEqual(query, {"from_unit": "m", "to_unit": "km"})

    def test_unknown_tool_is_an_envelope_not_an_exception(self):
        payload = json.loads(tools.unknown_tool_envelope("dsail_nope"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")
        self.assertIn("versions", payload)


if __name__ == "__main__":
    unittest.main()
