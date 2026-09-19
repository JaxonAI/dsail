"""The command line: offline commands, exit codes, and the egress relay."""

import io
import json
import os
import socket
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from dsail import cli, contract
from dsail._version import __version__


def _closed_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class OfflineCommandTests(unittest.TestCase):
    def test_version_reports_package_and_bundle(self):
        code, out, _ = _run(["version"])
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["dsail"], __version__)
        self.assertEqual(payload["contract"], contract.versions())

    def test_openapi_dumps_the_bundle(self):
        code, out, _ = _run(["openapi"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(json.loads(out)["paths"].keys(), contract.openapi()["paths"].keys())
        code, out, _ = _run(["openapi", "--path"])
        self.assertTrue(out.strip().endswith("openapi.json"))

    def test_init_writes_into_the_given_directory(self):
        root = tempfile.mkdtemp(prefix="dsail-cli-init-")
        code, out, _ = _run(["init", root, "--no-agents-md"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(os.path.exists(os.path.join(root, ".claude", "skills", "dsail", "SKILL.md")))
        self.assertTrue(os.path.exists(os.path.join(root, "CLAUDE.md")))
        self.assertFalse(os.path.exists(os.path.join(root, "AGENTS.md")))
        self.assertTrue(os.path.exists(os.path.join(root, ".agents", "skills", "dsail", "SKILL.md")))
        self.assertTrue(os.path.exists(os.path.join(root, ".codex", "config.toml")))
        self.assertIn("created", out)

    def test_init_no_codex_leaves_the_codex_files_out(self):
        root = tempfile.mkdtemp(prefix="dsail-cli-init-")
        code, _, _ = _run(["init", root, "--no-codex"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertFalse(os.path.exists(os.path.join(root, ".agents")))
        self.assertFalse(os.path.exists(os.path.join(root, ".codex")))

    def test_codex_plugin_builds_the_bundle(self):
        root = tempfile.mkdtemp(prefix="dsail-cli-plugin-")
        code, out, _ = _run(["codex-plugin", root, "--app-id", "asdk_app_test"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(os.path.exists(os.path.join(root, ".agents", "plugins", "marketplace.json")))
        self.assertTrue(os.path.exists(os.path.join(root, "plugins", "dsail", ".codex-plugin", "plugin.json")))
        self.assertTrue(os.path.exists(os.path.join(root, "plugins", "dsail", ".app.json")))
        self.assertIn("codex plugin marketplace add", out)
        code, out, _ = _run(["codex-plugin", root])
        self.assertFalse(os.path.exists(os.path.join(root, "plugins", "dsail", ".app.json")))
        self.assertIn("No --app-id given", out)

    def test_plugin_bundle_builds_both_marketplaces(self):
        root = tempfile.mkdtemp(prefix="dsail-cli-bundle-")
        code, out, _ = _run(["plugin-bundle", root])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(os.path.exists(os.path.join(root, ".claude-plugin", "marketplace.json")))
        self.assertTrue(os.path.exists(os.path.join(root, ".agents", "plugins", "marketplace.json")))
        self.assertTrue(os.path.exists(os.path.join(root, "plugins", "dsail", ".claude-plugin", "plugin.json")))
        self.assertTrue(os.path.exists(os.path.join(root, "plugins", "dsail", ".codex-plugin", "plugin.json")))
        self.assertIn("/plugin marketplace add", out)
        self.assertIn("codex plugin marketplace add", out)

    def test_credential_round_trip_in_a_private_config_dir(self):
        config = tempfile.mkdtemp(prefix="dsail-cred-")
        with mock.patch.dict(os.environ, {"DSAIL_CONFIG_DIR": config, "DSAIL_CREDENTIAL": ""}):
            code, out, _ = _run(["credential", "set", "tok-abcdefgh-1234"])
            self.assertEqual(code, cli.EXIT_OK)
            self.assertEqual(oct(os.stat(os.path.join(config, "credential")).st_mode & 0o777), "0o600")
            _, out, _ = _run(["credential", "show"])
            self.assertNotIn("abcdefgh", out)
            _, out, _ = _run(["credential", "show", "--reveal"])
            self.assertEqual(out.strip(), "tok-abcdefgh-1234")
            _, out, _ = _run(["credential", "forget"])
            self.assertEqual(out.strip(), "removed")
            _, out, _ = _run(["credential", "show"])
            self.assertIn("no credential", out)


class ExitCodeTests(unittest.TestCase):
    def test_unreachable_service_exits_3_with_a_sentence(self):
        url = "http://127.0.0.1:%d" % _closed_port()
        clean = {name: "" for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")}
        with mock.patch.dict(os.environ, clean):
            code, _, err = _run(["--url", url, "health"])
        self.assertEqual(code, cli.EXIT_UNREACHABLE)
        self.assertIn(url, err)

    def test_blocked_egress_exits_3_and_prints_the_relay_text(self):
        proxy = "http://127.0.0.1:%d" % _closed_port()
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "DSAIL_AGENT_ENV": "claude"}):
            code, _, err = _run(["--url", "https://agents.example", "health"])
        self.assertEqual(code, cli.EXIT_UNREACHABLE)
        self.assertIn("Enable the DSAIL connector", err)
        self.assertIn("agents.example", err)
        self.assertNotIn("Codex", err)

    def test_blocked_egress_in_a_codex_sandbox_names_the_codex_fix_only(self):
        proxy = "http://127.0.0.1:%d" % _closed_port()
        env = {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "DSAIL_AGENT_ENV": "",
               "CODEX_SANDBOX_NETWORK_DISABLED": "1"}
        with mock.patch.dict(os.environ, env):
            code, _, err = _run(["--url", "https://agents.example", "health"])
        self.assertEqual(code, cli.EXIT_UNREACHABLE)
        self.assertIn("internet-access allowlist", err)
        self.assertIn("agents.example", err)
        self.assertNotIn("Enable the DSAIL connector", err)
        self.assertNotIn("claude.ai", err)

    def test_missing_file_is_a_usage_error(self):
        code, _, err = _run(["--url", "http://127.0.0.1:1", "compile", "/nonexistent/file.dsail"])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("No such file", err)


if __name__ == "__main__":
    unittest.main()
