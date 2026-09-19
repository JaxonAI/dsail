"""`dsail init` writes every artifact, idempotently, and preserves what is there;
`dsail codex-plugin` builds the plugin bundle from the same pieces."""

import json
import os
import tempfile
import unittest

from dsail import contract, scaffold
from dsail._version import __version__

ALL_FILES = {
    scaffold.SKILL_RELATIVE,
    scaffold.CODEX_SKILL_RELATIVE,
    ".mcp.json",
    scaffold.CODEX_CONFIG,
    "CLAUDE.md",
    "AGENTS.md",
}

# The package root: this test file is <root>/test/test_scaffold.py. The public
# repository's root carries the plugin bundle, and it is the same tree.
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUNDLE_FILES = (
    scaffold.CLAUDE_MARKETPLACE_RELATIVE,
    scaffold.PLUGIN_MARKETPLACE_RELATIVE,
    os.path.join("plugins", "dsail", scaffold.CLAUDE_PLUGIN_MANIFEST_RELATIVE),
    os.path.join("plugins", "dsail", scaffold.PLUGIN_MANIFEST_RELATIVE),
    os.path.join("plugins", "dsail", ".mcp.json"),
    os.path.join("plugins", "dsail", "README.md"),
    os.path.join("plugins", "dsail", "skills", "dsail", "SKILL.md"),
)


class InitTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dsail-init-")

    def _read(self, relative):
        with open(os.path.join(self.root, relative), "r", encoding="utf-8") as handle:
            return handle.read()

    def _write(self, relative, text):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_fresh_repo_gets_both_skills_both_mcp_configs_and_both_stanzas(self):
        outcomes = scaffold.init(self.root)
        self.assertEqual(set(outcomes.values()), {"created"})
        self.assertEqual(set(outcomes), ALL_FILES)

        skill = self._read(scaffold.SKILL_RELATIVE)
        authoring = contract.authoring()
        self.assertTrue(skill.startswith("---\nname: dsail\n"))
        self.assertIn(authoring["authoring_sequence"].strip().splitlines()[0], skill)
        self.assertIn(authoring["grammar_guide"].strip().splitlines()[0], skill)
        self.assertIn(authoring["integrity_rule"].strip(), skill)
        self.assertIn("dsail serve", skill)
        # The Codex skill is the same bytes at the path Codex reads.
        self.assertEqual(skill, self._read(scaffold.CODEX_SKILL_RELATIVE))

        mcp = json.loads(self._read(".mcp.json"))
        self.assertEqual(mcp["mcpServers"]["dsail"], {"command": "dsail", "args": ["mcp"]})

        toml = self._read(scaffold.CODEX_CONFIG)
        self.assertIn(scaffold.TOML_START, toml)
        self.assertIn("[mcp_servers.dsail]", toml)
        self.assertIn('command = "dsail"', toml)
        self.assertIn('args = ["mcp"]', toml)
        self.assertNotIn("env =", toml)

        for name in ("CLAUDE.md", "AGENTS.md"):
            stanza = self._read(name)
            self.assertIn(scaffold.START, stanza)
            self.assertIn(scaffold.END, stanza)
            self.assertIn(".agents/skills/dsail/SKILL.md", stanza)
            self.assertIn(".claude/skills/dsail/SKILL.md", stanza)

    def test_the_stanza_and_skill_carry_the_no_mcp_path_explicitly(self):
        """A Codex cloud task has no MCP layer and no tool list to notice DSAIL in;
        the repo text is its whole discovery, so it must say: use the CLI or the
        client, unprompted."""
        for text in (scaffold.stanza(), scaffold.skill_markdown()):
            self.assertIn("no MCP layer", text)
            self.assertIn("dsail.Client", text)
            self.assertIn("pip install dsail", text)
        self.assertIn("without asking", scaffold.stanza())
        self.assertIn("Codex cloud", scaffold.stanza())

    def test_rerun_is_unchanged_everywhere(self):
        scaffold.init(self.root)
        outcomes = scaffold.init(self.root)
        self.assertEqual(set(outcomes.values()), {"unchanged"})

    def test_existing_mcp_json_entries_are_preserved(self):
        self._write(".mcp.json", json.dumps({"mcpServers": {"other": {"command": "x"}}, "extra": 1}))
        scaffold.init(self.root, url="https://dsail.example")
        mcp = json.loads(self._read(".mcp.json"))
        self.assertEqual(mcp["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(mcp["extra"], 1)
        self.assertEqual(mcp["mcpServers"]["dsail"]["env"], {"DSAIL_URL": "https://dsail.example"})
        toml = self._read(scaffold.CODEX_CONFIG)
        self.assertIn('env = { DSAIL_URL = "https://dsail.example" }', toml)

    def test_existing_codex_config_keeps_its_tables_and_gets_one_block(self):
        original = 'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "x"\n'
        self._write(scaffold.CODEX_CONFIG, original)
        scaffold.init(self.root)
        first = self._read(scaffold.CODEX_CONFIG)
        self.assertTrue(first.startswith(original))
        self.assertEqual(first.count("[mcp_servers.dsail]"), 1)
        self.assertEqual(first.count(scaffold.TOML_START), 1)

        edited = first.replace('args = ["mcp"]', 'args = ["OLD"]') + "\n[profiles.x]\ny = 1\n"
        self._write(scaffold.CODEX_CONFIG, edited)
        outcomes = scaffold.init(self.root)
        self.assertEqual(outcomes[scaffold.CODEX_CONFIG], "updated")
        final = self._read(scaffold.CODEX_CONFIG)
        self.assertNotIn("OLD", final)
        self.assertEqual(final.count("[mcp_servers.dsail]"), 1)
        self.assertTrue(final.startswith(original))
        self.assertIn("[profiles.x]\ny = 1", final)

    def test_existing_claude_md_keeps_its_content_and_gets_one_stanza(self):
        original = "# My project\n\nDo the thing.\n"
        self._write("CLAUDE.md", original)
        scaffold.init(self.root)
        first = self._read("CLAUDE.md")
        self.assertTrue(first.startswith(original))
        self.assertEqual(first.count(scaffold.START), 1)

        # A later package with different stanza text replaces the block in place.
        edited = first.replace("policy verification", "OLD TEXT")
        self._write("CLAUDE.md", edited + "\n## Afterwards\nkept\n")
        outcomes = scaffold.init(self.root)
        self.assertEqual(outcomes["CLAUDE.md"], "updated")
        final = self._read("CLAUDE.md")
        self.assertNotIn("OLD TEXT", final)
        self.assertEqual(final.count(scaffold.START), 1)
        self.assertTrue(final.startswith(original))
        self.assertIn("## Afterwards\nkept", final)

    def test_flags_limit_what_is_written(self):
        outcomes = scaffold.init(self.root, write_skill=False, write_mcp=False, stanza_files=("AGENTS.md",))
        self.assertEqual(set(outcomes), {"AGENTS.md"})
        outcomes = scaffold.init(self.root, write_codex=False, stanza_files=())
        self.assertEqual(set(outcomes), {scaffold.SKILL_RELATIVE, ".mcp.json"})


class CodexPluginTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dsail-plugin-")

    def _json(self, relative):
        with open(os.path.join(self.root, relative), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _text(self, relative):
        with open(os.path.join(self.root, relative), "r", encoding="utf-8") as handle:
            return handle.read()

    def test_the_bundle_has_the_marketplace_layout_and_the_skill_is_the_init_skill(self):
        outcomes = scaffold.codex_plugin(self.root)
        self.assertEqual(set(outcomes.values()), {"created"})
        marketplace = self._json(scaffold.PLUGIN_MARKETPLACE_RELATIVE)
        self.assertEqual(marketplace["name"], scaffold.MARKETPLACE_NAME)
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "dsail")
        self.assertEqual(entry["source"], {"source": "local", "path": "./plugins/dsail"})
        self.assertIn(entry["policy"]["installation"], ("AVAILABLE", "INSTALLED_BY_DEFAULT"))

        manifest = self._json(os.path.join("plugins", "dsail", scaffold.PLUGIN_MANIFEST_RELATIVE))
        self.assertEqual(manifest["name"], "dsail")
        self.assertEqual(manifest["version"], __version__)
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertNotIn("apps", manifest, "no connector without an app id")
        self.assertEqual(manifest["description"], contract.phrasing()["lead"])
        self.assertEqual(manifest["interface"]["displayName"], "DSAIL")
        self.assertTrue(manifest["interface"]["websiteURL"].startswith(contract.docs_url()))

        mcp = self._json(os.path.join("plugins", "dsail", ".mcp.json"))
        self.assertEqual(mcp, {"mcpServers": {"dsail": {"command": "dsail", "args": ["mcp"]}}})
        self.assertFalse(os.path.exists(os.path.join(self.root, "plugins", "dsail", ".app.json")))

        self.assertEqual(self._text(os.path.join("plugins", "dsail", "skills", "dsail", "SKILL.md")),
                         scaffold.skill_markdown())
        readme = self._text(os.path.join("plugins", "dsail", "README.md"))
        self.assertIn(contract.phrasing()["lead"], readme)
        self.assertIn("codex plugin marketplace add", readme)

    def test_an_app_id_bundles_the_connector_and_its_absence_removes_it(self):
        scaffold.codex_plugin(self.root, app_id="asdk_app_0123")
        manifest = self._json(os.path.join("plugins", "dsail", scaffold.PLUGIN_MANIFEST_RELATIVE))
        self.assertEqual(manifest["apps"], "./.app.json")
        app = self._json(os.path.join("plugins", "dsail", ".app.json"))
        self.assertEqual(app, {"apps": {"dsail": {"id": "asdk_app_0123", "required": False}}})

        outcomes = scaffold.codex_plugin(self.root)
        self.assertEqual(outcomes[os.path.join("plugins", "dsail", ".app.json")], "removed")
        self.assertNotIn("apps", self._json(os.path.join("plugins", "dsail", scaffold.PLUGIN_MANIFEST_RELATIVE)))

    def test_a_pinned_url_reaches_the_bundled_server_entry(self):
        scaffold.codex_plugin(self.root, url="https://dsail.example")
        mcp = self._json(os.path.join("plugins", "dsail", ".mcp.json"))
        self.assertEqual(mcp["mcpServers"]["dsail"]["env"], {"DSAIL_URL": "https://dsail.example"})

    def test_rebuild_is_unchanged(self):
        scaffold.codex_plugin(self.root, app_id="asdk_app_0123")
        outcomes = scaffold.codex_plugin(self.root, app_id="asdk_app_0123")
        self.assertEqual(set(outcomes.values()), {"unchanged"})


class PluginBundleTests(unittest.TestCase):
    """One plugin directory, two marketplaces — and the committed copy is generated."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dsail-bundle-")

    def _json(self, root, relative):
        with open(os.path.join(root, relative), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _text(self, root, relative):
        with open(os.path.join(root, relative), "r", encoding="utf-8") as handle:
            return handle.read()

    def test_the_bundle_carries_both_marketplaces_over_one_plugin(self):
        outcomes = scaffold.plugin_bundle(self.root)
        self.assertEqual(set(outcomes), set(BUNDLE_FILES))
        self.assertEqual(set(outcomes.values()), {"created"})
        lead = contract.phrasing()["lead"]

        marketplace = self._json(self.root, scaffold.CLAUDE_MARKETPLACE_RELATIVE)
        self.assertEqual(marketplace["name"], scaffold.MARKETPLACE_NAME)
        self.assertEqual(marketplace["owner"]["name"], "Jaxon, Inc.")
        self.assertEqual(marketplace["metadata"], {"pluginRoot": "./plugins"})
        (entry,) = marketplace["plugins"]
        self.assertEqual(entry["name"], "dsail")
        self.assertEqual(entry["source"], "./plugins/dsail")
        self.assertEqual(entry["description"], lead)
        self.assertEqual(entry["version"], __version__)
        self.assertEqual(entry["homepage"], contract.docs_url())
        self.assertEqual(entry["repository"], scaffold.PUBLIC_REPOSITORY)
        for tag in entry["tags"]:
            self.assertIn(tag, contract.phrasing()["trigger_phrases"].values())

        manifest = self._json(self.root, os.path.join("plugins", "dsail", scaffold.CLAUDE_PLUGIN_MANIFEST_RELATIVE))
        self.assertEqual(manifest["name"], "dsail")
        self.assertEqual(manifest["version"], __version__)
        self.assertEqual(manifest["description"], lead)
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")

        # The Codex half is untouched by the Claude half.
        codex = self._json(self.root, os.path.join("plugins", "dsail", scaffold.PLUGIN_MANIFEST_RELATIVE))
        self.assertEqual(codex["version"], __version__)
        readme = self._text(self.root, os.path.join("plugins", "dsail", "README.md"))
        self.assertIn("/plugin marketplace add JaxonAI/dsail", readme)
        self.assertIn("/plugin install dsail@jaxon", readme)
        self.assertIn("codex plugin marketplace add", readme)
        self.assertNotIn(self.root, readme, "the README must not carry a build machine's path")

    def test_the_committed_bundle_is_what_the_package_generates(self):
        """The public repository's root IS the bundle; it must be regenerated with the version.

        `dsail plugin-bundle sdk/dsail` refreshes it. Skipped when the bundle is
        not beside this test (an installed wheel), never when it is stale.
        """
        committed = os.path.join(PACKAGE_ROOT, scaffold.CLAUDE_MARKETPLACE_RELATIVE)
        if not os.path.exists(committed):
            self.skipTest("no committed bundle beside this test")
        scaffold.plugin_bundle(self.root)
        for relative in BUNDLE_FILES:
            with self.subTest(file=relative):
                self.assertEqual(
                    self._text(self.root, relative), self._text(PACKAGE_ROOT, relative),
                    "%s is stale: run `dsail plugin-bundle sdk/dsail` and commit" % relative,
                )

    def test_rebuild_is_unchanged(self):
        scaffold.plugin_bundle(self.root)
        self.assertEqual(set(scaffold.plugin_bundle(self.root).values()), {"unchanged"})


if __name__ == "__main__":
    unittest.main()
