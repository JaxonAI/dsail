"""dsail — the thin client for the DSAIL hosted service.

The package contains no parser, no compiler and no solver. Everything formal
happens on the hosted service; what ships here is the way to reach it:

* :class:`Client` — the REST client, with typed results and the retry-on-
  validation-error loop an extraction pipeline needs.
* ``dsail mcp`` — a stdio MCP proxy a coding agent registers in its own
  configuration; it forwards tool calls to the service over HTTPS.
* ``dsail serve`` — the review UI, served at a localhost link, for clients
  that do not render MCP Apps.
* ``dsail init`` — writes the skill and agent-instructions stanza into a repo.
* :mod:`dsail.contract` — the bundled OpenAPI document and JSON schemas.

The service never calls a language model. Extraction runs on your model,
against the prompt pack the service generates; this package carries the claim
dictionary you assemble to the service and brings back what the rules
concluded — TRUE, FALSE, UNKNOWN or AMBIGUOUS per assertion, never a verdict.
"""

from dsail._version import __version__
from dsail.client import DEFAULT_URL, Client
from dsail.errors import (
    BadRequest,
    BudgetExceeded,
    CompileFailed,
    CredentialRequired,
    CredentialScopeExceeded,
    DSAILError,
    EgressBlocked,
    EvaluationLimitReached,
    RulesetNotFound,
    ServiceError,
    ServiceUnreachable,
    ValidationRejected,
)
from dsail.types import (
    AMBIGUOUS,
    FALSE,
    RESULTS,
    TRUE,
    UNKNOWN,
    AssertionResult,
    CheckResult,
    ClaimPrompt,
    CompileResult,
    Failure,
    PromptPack,
    RuleResult,
)

__all__ = [
    "__version__",
    "DEFAULT_URL",
    "Client",
    "DSAILError",
    "EgressBlocked",
    "ServiceUnreachable",
    "ServiceError",
    "ValidationRejected",
    "CompileFailed",
    "BudgetExceeded",
    "RulesetNotFound",
    "BadRequest",
    "EvaluationLimitReached",
    "CredentialRequired",
    "CredentialScopeExceeded",
    "TRUE",
    "FALSE",
    "UNKNOWN",
    "AMBIGUOUS",
    "RESULTS",
    "AssertionResult",
    "RuleResult",
    "CheckResult",
    "CompileResult",
    "ClaimPrompt",
    "PromptPack",
    "Failure",
]
