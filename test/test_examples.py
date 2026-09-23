"""The sample integration runs against a real service and decides correctly."""

import importlib.util
import os
import unittest

import dsail
from dsail.client import Client

from test import _live

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
EXAMPLE = os.path.join(EXAMPLES_DIR, "expense_service.py")
ADVERSE_ACTION_EXAMPLE = os.path.join(EXAMPLES_DIR, "adverse_action.py")


def _load_example(path=EXAMPLE):
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
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


class AdverseActionNoticeTests(unittest.TestCase):
    """The regulation-keyed example (TJP-709): one rule per clause of 12 CFR
    1002.9, named for it, and a gate that decides what a FALSE costs."""

    CLEAN = {"days_to_notice": 12, "states_action_taken": True, "names_creditor": True,
             "ecoa_notice_present": True,
             "reasons": ["Insufficient income for the amount requested",
                         "Length of time accounts have been established"],
             "reasons_are_specific": True, "decision_basis": "scoring",
             "reasons_are_scored_factors": True}

    @classmethod
    def setUpClass(cls):
        cls.url = _live.live_url()
        if not cls.url:
            raise unittest.SkipTest(_live.reason())
        cls.module = _load_example(ADVERSE_ACTION_EXAMPLE)
        cls.gate = cls.module.NoticeGate(
            cls.module.structured_notice_extractor, client=Client(url=cls.url)
        )

    def test_every_rule_is_named_for_its_clause(self):
        names = sorted(self.gate.decide(self.CLEAN).results)
        self.assertEqual(
            ["a1_notice_within_thirty_days", "a2_names_creditor", "a2_states_action_taken",
             "b1_ecoa_notice_present", "b2_at_least_one_reason", "b2_no_more_than_four_reasons",
             "b2_reasons_are_specific", "b2_scoring_reasons_are_scored_factors"],
            names,
        )
        self.assertEqual(8, len(self.gate.pack.empty_claims()))

    def test_a_clean_notice_is_sent(self):
        decision = self.gate.decide(self.CLEAN)
        self.assertEqual("send", decision.action)
        self.assertEqual({dsail.TRUE}, set(decision.results.values()))

    def test_a_late_notice_with_six_reasons_goes_back_for_revision(self):
        decision = self.gate.decide(
            {"days_to_notice": 34, "states_action_taken": True, "names_creditor": True,
             "ecoa_notice_present": True,
             "reasons": ["a", "b", "c", "d", "e", "Did not meet our credit scoring cutoff"],
             "reasons_are_specific": False, "decision_basis": "scoring",
             "reasons_are_scored_factors": False}
        )
        self.assertEqual("revise", decision.action)
        for name in ("a1_notice_within_thirty_days", "b2_reasons_are_specific",
                     "b2_no_more_than_four_reasons", "b2_scoring_reasons_are_scored_factors"):
            self.assertEqual(dsail.FALSE, decision.results[name], name)
            self.assertIn(name, decision.counterexamples)
        self.assertEqual(dsail.TRUE, decision.results["b2_at_least_one_reason"])

    def test_a_judgmental_decision_does_not_need_the_scored_factors(self):
        notice = dict(self.CLEAN, decision_basis="judgmental")
        notice.pop("reasons_are_scored_factors")
        decision = self.gate.decide(notice)
        self.assertEqual("send", decision.action)
        self.assertEqual(dsail.TRUE, decision.results["b2_scoring_reasons_are_scored_factors"])

    def test_a_missing_decision_record_holds_the_notice(self):
        notice = dict(self.CLEAN)
        notice.pop("reasons_are_scored_factors")
        decision = self.gate.decide(notice)
        self.assertEqual("hold", decision.action)
        self.assertIn("reasons_are_scored_factors", decision.unbound_claims)
        self.assertEqual(dsail.UNKNOWN, decision.results["b2_scoring_reasons_are_scored_factors"])
        self.assertEqual(dsail.TRUE, decision.results["a1_notice_within_thirty_days"])


if __name__ == "__main__":
    unittest.main()
