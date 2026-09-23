"""A regulation-keyed integration: adverse action notices under Regulation B.

The same four steps as ``expense_service.py``, on a policy that is a written
regulatory procedure rather than an internal expense rule. A lender declining
a credit application must tell the applicant the principal reasons, and those
reasons must be the actual ones (12 CFR 1002.9). The procedure a compliance
team writes to meet that lives in ``policies/adverse_action.dsail``, one rule
per clause, and this gate checks every outgoing notice against it before it is
sent.

1. **Compile** the procedure and keep its hash. The hash is what a later
   re-check, or an examiner's question months on, runs against.
2. **Fetch the prompt pack** and run extraction on *this lender's own model*
   over the notice and the decision record. Jaxon never sees either.
3. **Check** the claim dictionary, repairing validation failures by re-asking
   for exactly the fields the service named.
4. **Act on per-assertion results.** The service says what each rule
   concluded; this gate decides what that costs: a FALSE sends the notice back
   for revision, an UNKNOWN holds it until the missing fact is in front of
   someone.

The extractor is a stand-in that reads a structured record, so the example
runs without a model. Swap :class:`Extractor` for a call to your model.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import dsail

POLICY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policies", "adverse_action.dsail")

#: ``extract(prompt_text, document) -> value``. Your model goes here.
Extractor = Callable[[str, Any], Any]


@dataclass
class Decision:
    """What this gate concluded about one notice, from the rules' own words."""

    ruleset_hash: str
    results: Dict[str, str]
    counterexamples: Dict[str, str]
    unbound_claims: List[str]
    action: str  # "send" | "revise" | "hold"
    reasons: List[str] = field(default_factory=list)


class NoticeGate:
    """Checks adverse action notices against the compliance procedure in the repo."""

    def __init__(self, extractor: Extractor, client: Optional[dsail.Client] = None,
                 policy_path: str = POLICY_PATH):
        self.client = client or dsail.Client()
        self.extractor = extractor
        with open(policy_path, "r", encoding="utf-8") as handle:
            self.compiled = self.client.compile(handle.read())
        self.pack = self.client.prompt_pack(ruleset_hash=self.compiled.ruleset_hash)
        self._prompts = {prompt.claim: prompt for prompt in self.pack.prompts}

    def extract(self, document: Any) -> Dict[str, Any]:
        claims = self.pack.empty_claims()
        for prompt in self.pack.prompts:
            claims[prompt.claim] = self.extractor(prompt.render(), document)
        return claims

    def _repair(self, document: Any):
        def repair(current: Dict[str, Any], failures) -> Dict[str, Any]:
            fixed = dict(current)
            for failure in failures:
                prompt = self._prompts.get(failure.field)
                if prompt is None:
                    fixed.pop(failure.field, None)
                    continue
                text = prompt.render() + "\n\nYour previous answer was rejected: %s" % failure.expected
                fixed[failure.field] = self.extractor(text, document)
            return fixed

        return repair

    def decide(self, document: Any) -> Decision:
        claims = self.extract(document)
        result = self.client.check_with_repair(
            claims, self._repair(document), ruleset_hash=self.compiled.ruleset_hash
        )
        results = {a.name: a.check for a in result.assertions}
        counterexamples = {a.name: a.counterexample for a in result.assertions if a.counterexample}

        # The gate's own policy. The engine reported per-assertion conclusions
        # and stopped; what a FALSE costs an outgoing notice is decided here.
        if any(check == dsail.FALSE for check in results.values()):
            action = "revise"
            reasons = ["%s is FALSE" % name for name, check in results.items() if check == dsail.FALSE]
        elif any(check in (dsail.UNKNOWN, dsail.AMBIGUOUS) for check in results.values()):
            action = "hold"
            reasons = ["%s is %s" % (name, check) for name, check in results.items()
                       if check in (dsail.UNKNOWN, dsail.AMBIGUOUS)]
        else:
            action = "send"
            reasons = []
        return Decision(
            ruleset_hash=result.ruleset_hash,
            results=results,
            counterexamples=counterexamples,
            unbound_claims=result.unbound_claims,
            action=action,
            reasons=reasons,
        )


# ----------------------------------------------------------------------------
# A stand-in extractor. It reads a notice that is already structured and
# answers each prompt from the field the question names. The behaviour worth
# copying is the last line: when the answer is not there, say "unknown".
# ----------------------------------------------------------------------------

_FIELD_FOR_QUESTION = (
    (re.compile(r"days passed", re.I), "days_to_notice"),
    (re.compile(r"action the creditor took", re.I), "states_action_taken"),
    (re.compile(r"name and address", re.I), "names_creditor"),
    (re.compile(r"Equal Credit Opportunity", re.I), "ecoa_notice_present"),
    (re.compile(r"how many principal reasons", re.I), "reason_count"),
    (re.compile(r"specific to this application", re.I), "reasons_are_specific"),
    (re.compile(r"scoring system, by judgmental", re.I), "decision_basis"),
    (re.compile(r"actually scored", re.I), "reasons_are_scored_factors"),
)


def structured_notice_extractor(prompt_text: str, document: Dict[str, Any]) -> Any:
    for pattern, key in _FIELD_FOR_QUESTION:
        if pattern.search(prompt_text):
            if key == "reason_count" and isinstance(document.get("reasons"), list):
                return len(document["reasons"])
            value = document.get(key)
            if value is None:
                return dsail.types.UNDETERMINED
            return value
    return dsail.types.UNDETERMINED


if __name__ == "__main__":
    gate = NoticeGate(structured_notice_extractor)
    for notice in (
        # A clean notice from a scoring decision: two specific reasons, both
        # factors the system scored, sent on day 12.
        {"days_to_notice": 12, "states_action_taken": True, "names_creditor": True,
         "ecoa_notice_present": True,
         "reasons": ["Insufficient income for the amount requested",
                     "Length of time accounts have been established"],
         "reasons_are_specific": True, "decision_basis": "scoring",
         "reasons_are_scored_factors": True},
        # Six reasons, one of them "did not meet our credit scoring cutoff",
        # not all of them scored factors, sent on day 34: four rules answer
        # FALSE and the notice goes back.
        {"days_to_notice": 34, "states_action_taken": True, "names_creditor": True,
         "ecoa_notice_present": True,
         "reasons": ["Insufficient income", "Too many recent inquiries", "Limited credit history",
                     "High revolving balances", "Delinquent past obligations",
                     "Did not meet our credit scoring cutoff"],
         "reasons_are_specific": False, "decision_basis": "scoring",
         "reasons_are_scored_factors": False},
        # The decision record is not attached, so whether the reasons were the
        # scored factors cannot be determined: that one rule answers UNKNOWN
        # and the notice is held, not sent and not sent back.
        {"days_to_notice": 20, "states_action_taken": True, "names_creditor": True,
         "ecoa_notice_present": True,
         "reasons": ["Insufficient income for the amount requested"],
         "reasons_are_specific": True, "decision_basis": "scoring"},
    ):
        decision = gate.decide(notice)
        print(decision.action.upper(), "day %s, %d reason(s)" % (notice["days_to_notice"], len(notice["reasons"])))
        for name, check in decision.results.items():
            print("   %-40s %s" % (name, check))
        for reason in decision.reasons:
            print("   -> " + reason)
