"""Typed views expose the wire fields and hide nothing."""

import unittest

import dsail
from dsail.types import CheckResult, ClaimPrompt, CompileResult, PromptPack

CHECK = {
    "ok": True,
    "ruleset_hash": "h" * 64,
    "unit_library_hash": None,
    "rules": [
        {"rule": "within_cap", "assertions": [{"name": "within_cap", "check": "FALSE",
                                               "source": "assert within_cap { amount <= 5000 }"}]},
        {"rule": "needs_receipt", "assertions": [{"name": "needs_receipt", "check": "UNKNOWN",
                                                  "reason_unknown": "has_receipt unbound"}]},
        {"rule": "ok_rule", "assertions": [{"name": "ok_rule", "check": "TRUE"}]},
    ],
    "claims": {"bound": ["amount"], "unbound": ["has_receipt"],
               "quantities": [{"claim": "amount", "submitted": "6000 USD", "bound": '6000 "USD"', "measured_in": "USD"}]},
    "versions": {"wire_version": "2.1.0"},
    "a_field_this_build_never_heard_of": 42,
}


class CheckResultTests(unittest.TestCase):
    def test_assertions_carry_the_engines_words_only(self):
        result = CheckResult(CHECK)
        checks = [a.check for a in result.assertions]
        self.assertEqual(checks, ["FALSE", "UNKNOWN", "TRUE"])
        for check in checks:
            self.assertIn(check, dsail.RESULTS)
        self.assertFalse(hasattr(result, "verdict"))

    def test_where_and_by_name(self):
        result = CheckResult(CHECK)
        self.assertEqual([a.name for a in result.where(dsail.FALSE)], ["within_cap"])
        self.assertEqual(result.by_name()["within_cap"].source, "assert within_cap { amount <= 5000 }")
        self.assertFalse(hasattr(result.by_name()["within_cap"], "counterexample"))
        self.assertTrue(result.by_name()["ok_rule"].holds)
        self.assertTrue(result.by_name()["within_cap"].violated)

    def test_claims_and_quantities(self):
        result = CheckResult(CHECK)
        self.assertEqual(result.bound_claims, ["amount"])
        self.assertEqual(result.unbound_claims, ["has_receipt"])
        self.assertEqual(result.quantities[0]["measured_in"], "USD")

    def test_unknown_fields_survive_in_payload(self):
        result = CheckResult(CHECK)
        self.assertEqual(result["a_field_this_build_never_heard_of"], 42)
        self.assertEqual(result.versions["wire_version"], "2.1.0")


class CompileResultTests(unittest.TestCase):
    def test_accessors(self):
        result = CompileResult({
            "ok": True, "ruleset_hash": "x", "source": "src", "review": "ASKS 1 QUESTION",
            "summary": "s", "manifest": {"ruleset_hash": "x", "claims": [{"claim": "amount"}], "rules": []},
            "claim_schema": {"type": "object"}, "validation_contract": {}, "diagnostics": [],
            "unbridged_units": [{"rule": "r", "from": "USD", "to": "EUR", "cause": "missing"}],
            "versions": {},
        })
        self.assertEqual(result.claim_names, ["amount"])
        self.assertEqual(result.review, "ASKS 1 QUESTION")
        self.assertEqual(result.unbridged_units[0]["to"], "EUR")
        self.assertEqual(result.claim_schema["type"], "object")


class PromptPackTests(unittest.TestCase):
    PACK = {
        "ok": True, "ruleset_hash": "x", "integrity_rule": "unknown is a first-class answer.",
        "prompts": [
            {"claim": "amount", "data_type": "numeric", "question": "How much?", "answer_format": "a number in USD",
             "unknown_rule": 'Answer "unknown" if undetermined.', "unit": "USD", "range": {"min": 0, "max": 10}},
            {"claim": "category", "data_type": "enum", "question": "Which?", "answer_format": "one of the values",
             "unknown_rule": "...", "vocabulary": ["travel", "unknown"], "undetermined_value": None},
        ],
        "claim_schema": {}, "validation_contract": {}, "assembly_notes": ["note"], "versions": {},
    }

    def test_prompts_are_typed_and_render_every_published_field(self):
        pack = PromptPack(self.PACK)
        prompt = pack.prompts[0]
        self.assertIsInstance(prompt, ClaimPrompt)
        rendered = prompt.render()
        for expected in ("How much?", "a number in USD", "Answer in: USD", "Range:", "unknown"):
            self.assertIn(expected, rendered)
        self.assertIn("Allowed values: travel, unknown", pack.prompts[1].render())

    def test_empty_claims_uses_each_claims_undetermined_value(self):
        pack = PromptPack(self.PACK)
        # The second claim's vocabulary contains "unknown", so its sentinel is null.
        self.assertEqual(pack.empty_claims(), {"amount": "unknown", "category": None})
        self.assertEqual(pack.assembly_notes, ["note"])


if __name__ == "__main__":
    unittest.main()
