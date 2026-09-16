"""Which agent is running decides which egress fix is named — and only that one."""

import unittest

from dsail import environment
from dsail.errors import EgressBlocked, egress_blocked_message

CODEX_ONLY = ("Codex", "internet-access allowlist", "Environments")
CLAUDE_ONLY = ("Enable the DSAIL connector in claude.ai", "workspace network allowlist")


class DetectionTests(unittest.TestCase):
    def test_codex_from_its_sandbox_variables(self):
        for env in ({"CODEX_SANDBOX_NETWORK_DISABLED": "1"},
                    {"CODEX_ENV_PYTHON_VERSION": "3.12"}, {"CODEX_HOME": "/root/.codex"}):
            with self.subTest(env=env):
                self.assertEqual(environment.CODEX, environment.detect(env))

    def test_claude_from_its_shell_variables(self):
        for env in ({"CLAUDECODE": "1"}, {"CLAUDE_CODE_ENTRYPOINT": "cli"}):
            with self.subTest(env=env):
                self.assertEqual(environment.CLAUDE, environment.detect(env))

    def test_codex_cli_sandbox_is_codex_local(self):
        local = {"CODEX_SANDBOX": "seatbelt", "CODEX_SANDBOX_NETWORK_DISABLED": "1"}
        self.assertEqual(environment.CODEX_LOCAL, environment.detect(local))
        self.assertTrue(environment.codex_network_disabled(local))
        self.assertFalse(environment.codex_network_disabled({"CODEX_SANDBOX": "seatbelt"}))
        # The override names Codex; the flavor still comes from the sandbox variable.
        self.assertEqual(environment.CODEX_LOCAL,
                         environment.detect({"DSAIL_AGENT_ENV": "codex", "CODEX_SANDBOX": "landlock"}))
        self.assertEqual(environment.CODEX, environment.detect({"DSAIL_AGENT_ENV": "codex"}))

    def test_nothing_recognisable_is_unknown(self):
        self.assertEqual(environment.UNKNOWN, environment.detect({"PATH": "/bin", "HOME": "/h"}))

    def test_override_wins_and_garbage_is_ignored(self):
        self.assertEqual(environment.CODEX,
                         environment.detect({"CLAUDECODE": "1", "DSAIL_AGENT_ENV": "codex"}))
        self.assertEqual(environment.CLAUDE,
                         environment.detect({"CODEX_SANDBOX": "x", "DSAIL_AGENT_ENV": " Claude "}))
        self.assertEqual(environment.CODEX_LOCAL,
                         environment.detect({"CODEX_SANDBOX": "x", "DSAIL_AGENT_ENV": "vscode"}))


class EgressMessageTests(unittest.TestCase):
    def _message(self, which):
        return egress_blocked_message("https://agents.example/v1/check", "refused", "agents.example", which)

    def test_codex_names_the_codex_fix_and_no_other(self):
        text = self._message(environment.CODEX)
        for needle in CODEX_ONLY:
            self.assertIn(needle, text)
        for needle in CLAUDE_ONLY:
            self.assertNotIn(needle, text)
        self.assertIn("agents.example", text)
        self.assertIn("no MCP layer", text)
        self.assertIn("Nothing about your rules or claims was sent", text)

    def test_codex_local_sends_the_agent_to_the_mcp_tools(self):
        """Found live (2026-09-13): Codex CLI's sandbox has no network, so the
        CLI fails on DNS while the MCP tools, run outside the sandbox, work."""
        text = self._message(environment.CODEX_LOCAL)
        self.assertIn("sandbox", text)
        self.assertIn("dsail_compile", text)
        self.assertIn("/mcp", text)
        self.assertNotIn("internet-access allowlist", text)
        for needle in CLAUDE_ONLY:
            self.assertNotIn(needle, text)

    def test_claude_names_the_claude_fixes_and_no_codex_text(self):
        text = self._message(environment.CLAUDE)
        for needle in CLAUDE_ONLY:
            self.assertIn(needle, text)
        for needle in CODEX_ONLY:
            self.assertNotIn(needle, text)
        self.assertIn("Nothing about your rules or claims was sent", text)

    def test_unknown_says_it_could_not_tell_and_names_the_override(self):
        text = self._message(environment.UNKNOWN)
        self.assertIn("could not tell which agent", text)
        self.assertIn("DSAIL_AGENT_ENV", text)
        self.assertNotIn("Environments -> this environment", text)

    def test_the_exception_carries_the_environment_it_was_built_for(self):
        error = EgressBlocked("https://x/v1/a", "d", "x", agent_environment=environment.CODEX)
        self.assertEqual(environment.CODEX, error.agent_environment)
        self.assertIn("internet-access allowlist", str(error))
        self.assertEqual(("https://x/v1/a", "d", "x"), (error.url, error.detail, error.host))


if __name__ == "__main__":
    unittest.main()
