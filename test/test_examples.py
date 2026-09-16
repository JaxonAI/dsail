"""The sample integration runs against a real service and decides correctly."""

import importlib.util
import os
import unittest

import dsail
from dsail.client import Client

from test import _live

EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "examples", "expense_service.py")


def _load_example():
    spec = importlib.util.spec_from_file_location("expense_service", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExpenseServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = _live.live_url()
        if not cls.url:
            raise unittest.SkipTest(_live.reason())
        cls.module = _load_example()
        cls.gate = cls.module.ExpenseGate(
            cls.module.structured_record_extractor, client=Client(url=cls.url)
        )

    def test_compiles_the_repo_policy_and_fetches_its_prompt_pack(self):
        self.assertEqual(len(self.gate.compiled.ruleset_hash), 64)
        self.assertEqual(
            sorted(self.gate.pack.empty_claims()),
            ["amount", "category", "has_receipt", "manager_approved"],
        )

    def test_a_clean_report_is_paid(self):
        decision = self.gate.decide(
            {"amount": 42.10, "has_receipt": True, "category": "meals", "manager_approved": False}
        )
        self.assertEqual("pay", decision.action)
        self.assertEqual({dsail.TRUE}, set(decision.results.values()))

    def test_a_violation_routes_to_review_with_the_counterexample(self):
        decision = self.gate.decide(
            {"amount": 1899, "has_receipt": True, "category": "equipment", "manager_approved": False}
        )
        self.assertEqual("review", decision.action)
        self.assertEqual(dsail.FALSE, decision.results["equipment_needs_manager"])
        self.assertEqual(dsail.FALSE, decision.results["large_claims_need_manager"])
        self.assertIn("large_claims_need_manager", decision.counterexamples)
        self.assertEqual(dsail.TRUE, decision.results["within_cap"])

    def test_missing_evidence_holds_rather_than_guesses(self):
        decision = self.gate.decide({"amount": 1500, "has_receipt": True, "category": "travel"})
        self.assertEqual("hold", decision.action)
        self.assertIn("manager_approved", decision.unbound_claims)
        # The rules that do not need the missing claim still answer.
        self.assertEqual(dsail.TRUE, decision.results["receipt_over_75"])
        self.assertEqual(dsail.UNKNOWN, decision.results["large_claims_need_manager"])

    def test_validation_failures_are_repaired_by_re_asking(self):
        # An extractor that first answers with the wrong shape, then correctly:
        # the repair loop must re-ask only the rejected field.
        asked = []

        def flaky(prompt_text, document):
            value = self.module.structured_record_extractor(prompt_text, document)
            if "receipt" in prompt_text.lower():
                asked.append(prompt_text)
                if len(asked) == 1:
                    return "yes, definitely"  # not a boolean: rejected
            return value

        gate = self.module.ExpenseGate(flaky, client=Client(url=self.url))
        decision = gate.decide({"amount": 20, "has_receipt": True, "category": "meals",
                                "manager_approved": True})
        self.assertEqual("pay", decision.action)
        self.assertEqual(2, len(asked))
        self.assertIn("rejected", asked[1])


if __name__ == "__main__":
    unittest.main()
