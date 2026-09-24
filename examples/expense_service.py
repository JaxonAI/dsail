"""A production-shaped REST integration, end to end, in one file.

This is the code a coding agent writes into a customer's service after the
rules are authored and reviewed (TJP-621, acceptance criterion 6). It does the
four things every DSAIL integration does, and nothing the service does not
let it do:

1. **Compile** the policy that lives in this repository and keep its hash.
2. **Fetch the prompt pack** — one extraction prompt per claim — and run
   extraction on *this project's own model*, with this project's own
   credentials. Jaxon never sees the document, the key or the model choice;
   what crosses the wire is the claim dictionary the extractor assembled.
3. **Check** the dictionary, repairing validation failures by re-asking the
   model for exactly the fields the service named.
4. **Act on per-assertion results.** The service says what the rules
   concluded — TRUE, FALSE, UNKNOWN, AMBIGUOUS — and this service decides what
   that costs: here, a FALSE routes the report to a human and an UNKNOWN holds
   it for more evidence. That decision is deliberately ours, not the engine's.

The extractor here is a stand-in that reads a structured record, so the example
runs without a model. The seam is :class:`Extractor`: swap in a call to your
model and everything else stays as it is.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import dsail

POLICY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policies", "expenses.dsail")

#: ``extract(prompt_text, document) -> value``. Your model goes here. It answers
#: one question about one document; ``"unknown"`` is always an acceptable answer.
Extractor = Callable[[str, Any], Any]


@dataclass
class Decision:
    """What this service concluded about one expense report, from the rules' own words."""

    ruleset_hash: str
    results: Dict[str, str]
    violations: Dict[str, Optional[str]]  # FALSE assertion name -> its source text
    unbound_claims: List[str]
    action: str  # "pay" | "review" | "hold"
    reasons: List[str] = field(default_factory=list)


class ExpenseGate:
    """Checks expense reports against the repo's policy on the hosted service."""

    def __init__(self, extractor: Extractor, client: Optional[dsail.Client] = None,
                 policy_path: str = POLICY_PATH):
        self.client = client or dsail.Client()
        self.extractor = extractor
        with open(policy_path, "r", encoding="utf-8") as handle:
            self.compiled = self.client.compile(handle.read())
        self.pack = self.client.prompt_pack(ruleset_hash=self.compiled.ruleset_hash)
        self._prompts = {prompt.claim: prompt for prompt in self.pack.prompts}

    # -- step 2: extraction, on your model --------------------------------

    def extract(self, document: Any) -> Dict[str, Any]:
        claims = self.pack.empty_claims()  # every claim starts undetermined
        for prompt in self.pack.prompts:
            claims[prompt.claim] = self.extractor(prompt.render(), document)
        return claims

    def _repair(self, document: Any):
        def repair(current: Dict[str, Any], failures) -> Dict[str, Any]:
            fixed = dict(current)
            for failure in failures:
                prompt = self._prompts.get(failure.field)
                if prompt is None:
                    # A field the ruleset does not declare: drop it rather than
                    # invent a home for it.
                    fixed.pop(failure.field, None)
                    continue
                # Re-ask for exactly this field, with what the service expected.
                text = prompt.render() + "\n\nYour previous answer was rejected: %s" % failure.expected
                fixed[failure.field] = self.extractor(text, document)
            return fixed

        return repair

    # -- steps 3 and 4: check, then decide --------------------------------

    def decide(self, document: Any) -> Decision:
        claims = self.extract(document)
        result = self.client.check_with_repair(
            claims, self._repair(document), ruleset_hash=self.compiled.ruleset_hash
        )
        results = {a.name: a.check for a in result.assertions}
        violations = {a.name: a.source for a in result.assertions if a.violated}

        # This is the policy of THIS service, not of the engine: the engine
        # reported per-assertion conclusions and stopped.
        if any(check == dsail.FALSE for check in results.values()):
            action = "review"
            reasons = ["%s is FALSE" % name for name, check in results.items() if check == dsail.FALSE]
        elif any(check in (dsail.UNKNOWN, dsail.AMBIGUOUS) for check in results.values()):
            action = "hold"
            reasons = ["%s is %s" % (name, check) for name, check in results.items()
                       if check in (dsail.UNKNOWN, dsail.AMBIGUOUS)]
        else:
            action = "pay"
            reasons = []
        return Decision(
            ruleset_hash=result.ruleset_hash,
            results=results,
            violations=violations,
            unbound_claims=result.unbound_claims,
            action=action,
            reasons=reasons,
        )


# ----------------------------------------------------------------------------
# A stand-in extractor, so the example runs with no model. It reads a report
# that is already structured and answers each prompt from the field the
# question names. The only behaviour worth copying is the last line: when the
# answer is not there, say "unknown".
# ----------------------------------------------------------------------------

_FIELD_FOR_QUESTION = (
    (re.compile(r"total amount", re.I), "amount"),
    (re.compile(r"receipt", re.I), "has_receipt"),
    (re.compile(r"category", re.I), "category"),
    (re.compile(r"manager", re.I), "manager_approved"),
)


def structured_record_extractor(prompt_text: str, document: Dict[str, Any]) -> Any:
    for pattern, key in _FIELD_FOR_QUESTION:
        if pattern.search(prompt_text):
            value = document.get(key)
            if value is None:
                return dsail.types.UNDETERMINED
            if key == "amount" and isinstance(value, (int, float)):
                return "%s USD" % value
            return value
    return dsail.types.UNDETERMINED


if __name__ == "__main__":
    gate = ExpenseGate(structured_record_extractor)
    for report in (
        {"amount": 42.10, "has_receipt": False, "category": "meals", "manager_approved": False},
        {"amount": 1899, "has_receipt": True, "category": "equipment", "manager_approved": False},
        # No manager field at all: the extractor answers "unknown" and the one
        # rule that needs it answers UNKNOWN, so this report is held, not paid.
        {"amount": 1500, "has_receipt": True, "category": "travel"},
    ):
        decision = gate.decide(report)
        print(decision.action.upper(), report)
        for name, check in decision.results.items():
            print("   %-28s %s" % (name, check))
        for reason in decision.reasons:
            print("   -> " + reason)
