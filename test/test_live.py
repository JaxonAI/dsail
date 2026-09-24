"""The client against a real service: the whole authoring sequence, no mocks.

Skips with a reason when no service is reachable (see ``_live``). Everything
asserted here is a property of the wire contract the bundle was generated from.
"""

import unittest

import dsail
from dsail import credentials
from dsail.client import Client
from dsail.errors import (
    CompileFailed,
    CredentialRequired,
    CredentialScopeExceeded,
    RulesetNotFound,
    ValidationRejected,
)

from test import _live


class LiveClientTests(unittest.TestCase):
    """The evaluation grade: what a fresh install gets with no human gate."""

    @classmethod
    def setUpClass(cls):
        cls.url = _live.live_url()
        if not cls.url:
            raise unittest.SkipTest(_live.reason())
        cls.client = Client(url=cls.url, credential="")

    def test_first_contact_acquires_an_evaluation_credential_by_itself(self):
        credentials.forget()
        client = Client(url=self.url)
        self.assertIsNone(client.credential)
        compiled = client.compile(_live.DEMO_SOURCE)
        self.assertEqual(len(compiled.ruleset_hash), 64)
        self.assertTrue(client.credential.startswith("dsail_eval_"))
        self.assertEqual(credentials.resolve(), client.credential, "stored for later processes")
        account = client.account()
        self.assertEqual(account["credential"]["grade"], "evaluation")
        self.assertGreaterEqual(account["credential"]["usage"]["used"], 1)

    def test_storage_is_out_of_scope_for_an_evaluation_credential(self):
        with self.assertRaises(CredentialScopeExceeded) as caught:
            self.client.save_ruleset("sdk-eval-attempt", _live.DEMO_SOURCE)
        self.assertIn("full credential", str(caught.exception))

    def test_a_bad_credential_is_replaced_not_reported(self):
        client = Client(url=self.url, credential="dsail_eval_not-a-real-token")
        self.assertTrue(client.health()["ok"])
        self.assertTrue(client.version()["ok"])
        self.assertTrue(client.compile(_live.DEMO_SOURCE).ruleset_hash)
        self.assertNotEqual(client.credential, "dsail_eval_not-a-real-token")

    def test_with_auto_acquire_off_the_refusal_reaches_the_caller(self):
        client = Client(url=self.url, credential="dsail_eval_not-a-real-token", auto_credential=False)
        with self.assertRaises(CredentialRequired):
            client.compile(_live.DEMO_SOURCE)

    def test_version_and_health_answer(self):
        self.assertTrue(self.client.health()["ok"])
        versions = self.client.version()["versions"]
        self.assertIn("wire_version", versions)

    def test_compile_prompt_pack_check(self):
        compiled = self.client.compile(_live.DEMO_SOURCE)
        self.assertEqual(sorted(compiled.claim_names), ["amount", "category", "has_receipt"])
        self.assertIn("ASKS", compiled.review)
        self.assertEqual(len(compiled.ruleset_hash), 64)

        pack = self.client.prompt_pack(ruleset_hash=compiled.ruleset_hash)
        self.assertEqual(pack.ruleset_hash, compiled.ruleset_hash)
        prompts = {p.claim: p for p in pack.prompts}
        self.assertEqual(prompts["amount"].unit, "USD")
        self.assertIn("unknown", pack.integrity_rule)
        claims = pack.empty_claims()
        self.assertEqual(set(claims), {"amount", "category", "has_receipt"})

        claims.update({"amount": "6000 USD", "has_receipt": True, "category": "equipment"})
        result = self.client.check(claims, ruleset_hash=compiled.ruleset_hash)
        by_name = result.by_name()
        self.assertEqual(by_name["within_cap"].check, dsail.FALSE)
        self.assertIsNotNone(by_name["within_cap"].source)
        for rule in result.payload["rules"]:
            for entry in rule["assertions"]:
                self.assertNotIn("counterexample", entry)
        self.assertEqual(by_name["receipt_over_75"].check, dsail.TRUE)
        self.assertEqual(by_name["equipment_needs_receipt"].check, dsail.TRUE)
        for assertion in result.assertions:
            self.assertIn(assertion.check, dsail.RESULTS)

    def test_unknown_is_an_honest_unknown_not_an_error(self):
        compiled = self.client.compile(_live.DEMO_SOURCE)
        result = self.client.check(
            {"amount": "120 USD", "has_receipt": "unknown", "category": "meals"},
            ruleset_hash=compiled.ruleset_hash,
        )
        self.assertEqual(result.by_name()["receipt_over_75"].check, dsail.UNKNOWN)
        self.assertIn("has_receipt", result.unbound_claims)

    def test_validation_rejection_names_every_field_and_repair_converges(self):
        compiled = self.client.compile(_live.DEMO_SOURCE)
        bad = {"amount": "lots", "has_receipt": "maybe", "category": "snacks"}
        with self.assertRaises(ValidationRejected) as caught:
            self.client.check(bad, ruleset_hash=compiled.ruleset_hash)
        fields = sorted(f.field for f in caught.exception.failures)
        self.assertEqual(fields, ["amount", "category", "has_receipt"])

        def repair(current, failures):
            fixed = dict(current)
            for failure in failures:
                fixed[failure.field] = "unknown"
            return fixed

        result = self.client.check_with_repair(bad, repair, ruleset_hash=compiled.ruleset_hash)
        self.assertEqual({a.check for a in result.assertions}, {dsail.UNKNOWN})

    def test_compile_failure_carries_diagnostics_and_the_grammar(self):
        with self.assertRaises(CompileFailed) as caught:
            self.client.compile("version 1.3;\ndeclare x as numeric\nassert a { x > 1 };")
        self.assertTrue(caught.exception.diagnostics)
        self.assertIn("DSAIL ruleset grammar", caught.exception.hint)

    def test_units(self):
        library = self.client.unit_library()
        self.assertTrue(library["ok"])
        bridge = self.client.unit_bridge("m", "km")
        self.assertEqual(bridge["status"], "convertible")


class LiveFullGradeTests(unittest.TestCase):
    """Storage under an operator-issued full credential, in its own namespace."""

    @classmethod
    def setUpClass(cls):
        cls.url = _live.live_url()
        if not cls.url:
            raise unittest.SkipTest(_live.reason())
        token = _live.full_credential()
        if not token:
            raise unittest.SkipTest("no full credential for this service (DSAIL_TEST_FULL_CREDENTIAL)")
        cls.client = Client(url=cls.url, credential=token, auto_credential=False)

    def test_unknown_ruleset_is_not_found(self):
        with self.assertRaises(RulesetNotFound):
            self.client.load_ruleset("no-such-ruleset-%s" % ("x" * 8))

    def test_save_load_and_approve_round_trip(self):
        name = "sdk-live-expenses"
        saved = self.client.save_ruleset(name, _live.DEMO_SOURCE)
        self.assertTrue(saved["ok"], saved)
        loaded = self.client.load_ruleset(name)
        self.assertEqual(loaded["source"], self.client.compile(_live.DEMO_SOURCE).source)
        approved = self.client.record_approval(loaded["ruleset_hash"], "sdk test", note="live suite")
        self.assertTrue(approved["ok"], approved)
        names = [row["name"] for row in self.client.list_rulesets()["rulesets"]]
        self.assertIn(name, names)
        self.assertEqual(self.client.account()["credential"]["grade"], "full")


if __name__ == "__main__":
    unittest.main()
