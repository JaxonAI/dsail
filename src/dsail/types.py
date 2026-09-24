"""Typed views over the service's payloads.

Every class here wraps the raw payload and exposes the fields a caller reads
most, without hiding anything: ``payload`` is always the complete, unmodified
response. The wire contract is the truth and these are conveniences over it —
a field this build does not know about is still in ``payload``.

Results are the engine's own four words, and only those. There is no verdict
field because the service publishes none: what a FALSE should cost is the
caller's decision, and folding assertions is the caller's job.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

TRUE = "TRUE"
FALSE = "FALSE"
UNKNOWN = "UNKNOWN"
AMBIGUOUS = "AMBIGUOUS"

#: The complete result vocabulary. AMBIGUOUS is reserved on the wire — nothing
#: produces it from a claim dictionary yet — and is handled here so a later
#: release costs the caller no change.
RESULTS = (TRUE, FALSE, UNKNOWN, AMBIGUOUS)

#: The literal to submit for a claim you could not determine. Never a guess.
UNDETERMINED = "unknown"


@dataclass(frozen=True)
class Failure:
    """One field the validation contract rejected."""

    field: str
    expected: str
    received: str

    @classmethod
    def from_payload(cls, item):
        return cls(
            field=str(item.get("field", "")),
            expected=str(item.get("expected", "")),
            received=str(item.get("received", "")),
        )

    def as_dict(self):
        return {"field": self.field, "expected": self.expected, "received": self.received}


@dataclass(frozen=True)
class AssertionResult:
    """What the engine concluded about one assertion, and nothing derived."""

    rule: str
    name: str
    check: str
    reason_unknown: Optional[str] = None
    unbridged_units: List[Dict[str, str]] = field(default_factory=list)
    source: Optional[str] = None

    @property
    def holds(self):
        return self.check == TRUE

    @property
    def violated(self):
        return self.check == FALSE


@dataclass(frozen=True)
class RuleResult:
    """One rule with the assertions it states. Today one each; read through it anyway."""

    rule: str
    assertions: List[AssertionResult]


class _Wrapped:
    def __init__(self, payload):
        self.payload = payload

    def __getitem__(self, key):
        return self.payload[key]

    def get(self, key, default=None):
        return self.payload.get(key, default)

    @property
    def versions(self):
        return self.payload.get("versions") or {}

    def __repr__(self):
        return "%s(%r)" % (type(self).__name__, self.payload)


class CheckResult(_Wrapped):
    """``POST /v1/check``: every rule, each assertion's own result."""

    @property
    def ruleset_hash(self):
        return self.payload.get("ruleset_hash")

    @property
    def unit_library_hash(self):
        return self.payload.get("unit_library_hash")

    @property
    def rules(self):
        rules = []
        for rule in self.payload.get("rules") or []:
            assertions = [
                AssertionResult(
                    rule=rule.get("rule", ""),
                    name=item.get("name", ""),
                    check=item.get("check", UNKNOWN),
                    reason_unknown=item.get("reason_unknown"),
                    unbridged_units=list(item.get("unbridged_units") or []),
                    source=item.get("source"),
                )
                for item in rule.get("assertions") or []
            ]
            rules.append(RuleResult(rule=rule.get("rule", ""), assertions=assertions))
        return rules

    @property
    def assertions(self):
        """Every assertion across every rule, in wire order."""
        return [assertion for rule in self.rules for assertion in rule.assertions]

    def by_name(self):
        return {assertion.name: assertion for assertion in self.assertions}

    def where(self, check):
        """The assertions with a given result, e.g. ``result.where(dsail.FALSE)``."""
        return [assertion for assertion in self.assertions if assertion.check == check]

    @property
    def bound_claims(self):
        return list((self.payload.get("claims") or {}).get("bound") or [])

    @property
    def unbound_claims(self):
        return list((self.payload.get("claims") or {}).get("unbound") or [])

    @property
    def quantities(self):
        return list((self.payload.get("claims") or {}).get("quantities") or [])


class CompileResult(_Wrapped):
    """``POST /v1/compile``: the hash, the manifest, the contract check enforces."""

    @property
    def ruleset_hash(self):
        return self.payload.get("ruleset_hash")

    @property
    def source(self):
        return self.payload.get("source")

    @property
    def manifest(self):
        return self.payload.get("manifest") or {}

    @property
    def claims(self):
        return list(self.manifest.get("claims") or [])

    @property
    def claim_names(self):
        return [claim.get("claim") for claim in self.claims]

    @property
    def review(self):
        return self.payload.get("review") or ""

    @property
    def summary(self):
        return self.payload.get("summary") or ""

    @property
    def claim_schema(self):
        return self.payload.get("claim_schema") or {}

    @property
    def validation_contract(self):
        return self.payload.get("validation_contract") or {}

    @property
    def diagnostics(self):
        return list(self.payload.get("diagnostics") or [])

    @property
    def unbridged_units(self):
        return list(self.payload.get("unbridged_units") or [])


@dataclass(frozen=True)
class ClaimPrompt:
    """One extraction prompt: what to ask your model for one claim, and how to answer."""

    claim: str
    data_type: str
    question: str
    answer_format: str
    unknown_rule: str
    undetermined_value: Any = UNDETERMINED
    context: Optional[str] = None
    vocabulary: Optional[List[str]] = None
    ordered_vocabulary: Optional[bool] = None
    unit: Optional[str] = None
    range: Optional[Dict[str, Any]] = None

    @classmethod
    def from_payload(cls, item):
        return cls(
            claim=item.get("claim", ""),
            data_type=item.get("data_type", ""),
            question=item.get("question", ""),
            answer_format=item.get("answer_format", ""),
            unknown_rule=item.get("unknown_rule", ""),
            undetermined_value=item.get("undetermined_value", UNDETERMINED),
            context=item.get("context"),
            vocabulary=item.get("vocabulary"),
            ordered_vocabulary=item.get("ordered_vocabulary"),
            unit=item.get("unit"),
            range=item.get("range"),
        )

    def render(self):
        """The prompt as one block of text, ready for a model.

        Question, then the context, then the answer format and the unknown
        rule. Nothing here is invented: every line is a field the service
        published, in the order a reader needs them.
        """
        lines = [self.question]
        if self.context:
            lines.append("")
            lines.append("Context: %s" % self.context)
        lines.append("")
        lines.append("Answer format: %s" % self.answer_format)
        if self.vocabulary:
            lines.append("Allowed values: %s" % ", ".join(self.vocabulary))
        if self.unit:
            lines.append("Answer in: %s" % self.unit)
        if self.range:
            lines.append("Range: %s" % self.range)
        lines.append(self.unknown_rule)
        return "\n".join(lines)


class PromptPack(_Wrapped):
    """``/v1/prompt-pack``: one prompt per claim, the schema, the validation rules."""

    @property
    def ruleset_hash(self):
        return self.payload.get("ruleset_hash")

    @property
    def integrity_rule(self):
        return self.payload.get("integrity_rule") or ""

    @property
    def prompts(self):
        return [ClaimPrompt.from_payload(item) for item in self.payload.get("prompts") or []]

    @property
    def claim_schema(self):
        return self.payload.get("claim_schema") or {}

    @property
    def validation_contract(self):
        return self.payload.get("validation_contract") or {}

    @property
    def assembly_notes(self):
        return list(self.payload.get("assembly_notes") or [])

    def empty_claims(self):
        """A claim dictionary with every claim undetermined.

        The safe starting point for an extractor: fill in what the evidence
        supports and leave the rest, and the check answers UNKNOWN for the
        assertions that needed them rather than refusing or guessing.
        """
        return {prompt.claim: prompt.undetermined_value for prompt in self.prompts}
