---
name: dsail
description: Turn a written policy into rules a program can check, and get the same answer every time. Use when asked to enforce, check or encode a policy, rule, threshold or compliance requirement against facts extracted from documents; the check runs on the hosted DSAIL service with no model in the loop.
---

# DSAIL: policy to formal ruleset, with reproducible results

DSAIL turns a written policy into a formal ruleset and returns deterministic, reproducible results over claim values you extract: given these claim values under this ruleset, every assertion answers TRUE, FALSE, UNKNOWN or AMBIGUOUS — every time, and every result names the rule that decided. Whether the claim values faithfully describe the document is your extraction's responsibility. The service never calls a language model; you run extraction on the user's own model using the prompt pack it generates. Report results in those four words, attributed to the rules, and never as an overall verdict of your own.

Documentation for agents, every page as markdown: https://docs.agents.jaxon.ai (`https://docs.agents.jaxon.ai/llms.txt`
lists them all). Every structured error the service returns carries `docs`, the
URL of the page that resolves it — fetch it before retrying blind.

## How to work in this repository

- Rules live in the repo. Write the English policy summary and the DSAIL source
  as files next to the code they govern (for example `policies/<name>.md` and
  `policies/<name>.dsail`), commit them, and review them like code.
- The hosted service compiles and checks; nothing formal runs locally. Use the
  `dsail_*` MCP tools if they are connected, otherwise the CLI:

      dsail compile policies/<name>.dsail          # hash, manifest, review text
      dsail prompt-pack --hash <ruleset_hash>       # the extraction contract
      dsail check --hash <ruleset_hash> --claims claims.json
      dsail serve policies/<name>.dsail             # review UI at a localhost link

- No `dsail_*` tools in this session (a Codex cloud task has no MCP layer at
  all) means the CLI and the Python client ARE the path, not a fallback: the
  same operations, the same service, the same results. If `dsail` is not
  installed, `pip install dsail` first.
- Review is a human step, and this client renders no widget. When a ruleset is
  ready, call the `dsail_open_review` MCP tool (source, file path, or stored
  name): it starts the review UI on this machine, opens the browser, and
  returns a link — repeat that link to the user. Without MCP tools, give the
  user the exact command `dsail serve policies/<name>.dsail` to run in their
  own terminal; do not run it from a sandboxed shell, and never say a review
  panel is open unless you have a link to show. Approval there is recorded on
  the service against the exact hash.
- Integration code fetches the prompt pack, runs extraction on this project's
  own model and credentials, submits the claim dictionary with `dsail.Client`
  (`check_with_repair` handles validation failures), and acts on the
  per-assertion results. Jaxon never sees documents, keys or model choices.
- If a call fails saying outbound network access is blocked, relay that
  message to the user verbatim: it names the one-time fix for this environment.

Authoring sequence — follow it in this order:

1. dsail_compile(source)         -> ruleset_hash, claim manifest, claim schema,
                                    validation contract, diagnostics.
                                    On failure, read hint and fix the source.
2. dsail_get_prompt_pack(hash)   -> one extraction prompt per claim, plus the
                                    schema and the exact validation rules.
                                    You run the extraction on the user's own
                                    model; this service never calls an LLM.
3. dsail_check(hash, claims)     -> every rule with each assertion's own result
                                    (TRUE / FALSE / UNKNOWN / AMBIGUOUS), named
                                    and with its source text. One call
                                    validates AND solves.
4. dsail_save_ruleset(name, src) -> a named, immutable revision (parent-linked).
5. dsail_record_approval(hash)   -> binds a human approval to that exact hash.

Presenting: once step 1 SUCCEEDS, call dsail_review(hash) exactly once to show
the user the ruleset in the review widget (where it is also available). Never
call it for a compile that failed — fix the source and compile again; those
iterations are yours alone and render nothing. Call it again only for a later
revision you want the user to see.

Every object is addressed by the content hash of its source, so a hash is
proof of exactly which bytes produced a result.

BEFORE step 1, for every numeric claim: does the quantity have a unit? Money,
distance, weight, duration, data size — all do. If it does, declare it:

    // @unit amount USD
    declare amount as numeric;

This is not documentation. A numeric claim with no declared unit accepts only
bare numbers, so an extractor that answers "30000 EUR" is refused rather than
compared — and a policy written about dollars whose claim says nothing about
dollars cannot tell dollars from anything else. Declare the unit and the engine
converts what converts (2500 m against a km threshold), refuses what does not,
and tells you which pairs nothing bridges. Call dsail_unit_library if you are
unsure whether two units convert; never assume a currency rate.

THEN WRITE THAT UNIT ON EVERY LITERAL THE CLAIM IS COMPARED WITH. This is
enforced: a ruleset that compares a united claim against a bare number does not
compile.

    assert cap { amount <= 25000 "USD" };      -- compiles
    assert cap { amount <= 25000 };            -- REFUSED

Units live on literals, not on `declare`, and a bare literal adopts the unit of
whatever it meets. So the second form is not "25000 dollars": bind an answer of
24000 "EUR" and the threshold becomes 25000 EUR, the policy quietly
redenominates itself to the evidence, and 24000 <= 25000 comes back compliant
where the real question — is 25920 USD over 25000 USD — is not. Writing the
unit costs four characters and moves every conversion inside the solver, where
it is done in exact rationals.

Zero is not exempt: write 0 "USD". A dimensionless zero looks safe because
scaling leaves it at zero, but not every conversion is a scaling — 0 degC is
32 degF — and the rule is easier to follow than its exceptions.

Leave a number unsigned only when the quantity truly has no dimension: a count
of signatures, a position in an ordering, a ratio, a boolean-ish 0/1.

DSAIL ruleset grammar (v1.3), the subset this service compiles to SMT:

  version 1.3;                       -- optional; 1.2 and 1.3 are accepted

  declare <name> as boolean;         -- a yes/no claim
  declare <name> as numeric;         -- a number; units go on literals (below)
  declare <name> as enum ["a","b"];  -- ORDERED vocabulary (comparable with < >)
  declare <name> as enum {"a","b"};  -- UNORDERED vocabulary (== and != only)
  declare local <name> as boolean;   -- rule-local; NOT a claim, never extracted

  assert <name> { <expr> };              -- the rule. Holds => compliant.
  assert <name> [pessimistic] { ... };   -- unknown-resolution policy:
                                         -- optimistic | pessimistic | neutral

Enum members are double-quoted strings. Every statement ends with a semicolon.
You do not write `let` bindings for claim values — this service injects them
from the claim dictionary you submit to check.

Literals:
  numbers        42   3.5   0.05
  with a unit    3000 "mi"   25000 "USD"   0 "USD"
                 -- a number followed by the unit as a QUOTED string. A bare
                 -- unit word is a syntax error: 3000 mi fails to parse.
  booleans       True   False
                 -- capitalised. Lowercase true and false are read as
                 -- identifiers and fail as undefined variables.
  enum members   "low"   "high"            -- double-quoted strings

Expressions:
  comparison   ==  !=  <  <=  >  >=      (thresholds are STRICT: > means
                                          strictly greater, NOT at-or-above.
                                          Write >= if you mean at-or-above.
                                          Equality is ==, not =.)
  arithmetic   +  -  *  /  %
  LOGIC IS FUNCTION-STYLE, NOT INFIX. There is no `and`/`or`/`not` keyword:
               And(a, b, ...)     Or(a, b, ...)     Not(a)
               Xor(a, b)          Implies(a, b)     If(cond, a, b)
  conditional  IF <cond> THEN <expr> [ELSE <expr>] END
               CASE <subject> OF "x": <expr>, "y": <expr>, DEFAULT: <expr> END
  quantifiers  ForAll(x in s, p)      Exists(x in s, p)
               AtLeast(n, x in s, p)  AtMost(n, x in s, p)
               ExactlyOne(x in s, p)  CountWhere(x in s, p)
  sets         IsMember(v, s)  IsSubset(a, b)
               Union(a, b)  Intersect(a, b)  Difference(a, b)

What a check answers. Every assertion gets its own result, and that is the
whole answer:

  TRUE       the assertion holds
  FALSE      the assertion is violated
  UNKNOWN    a claim it needs was submitted as unknown, or is unconstrained
  AMBIGUOUS  reserved. It means an assertion's evidence contradicted
             itself, which needs several answers for one claim. A claim
             dictionary holds exactly one value per claim, so no check
             submitted to this service produces it. Handle the word if you
             switch on results; there is no input that triggers it.

There is no combined verdict, no severity and no ranking. What a violated
assertion should cost — a refusal, a human review, a note in a file — is a
decision for the system reading the result, and this service does not make it.

Host annotations. These are ordinary DSAIL comments, invisible to the compiler,
read by this service. Every one of them describes a CLAIM — what to ask for it
and what a valid answer looks like. None of them decides an outcome:

  // @ask <claim> What is ...?       -- the question shown to an extractor
  // @context <claim> <text>         -- extra context for the extractor
  // @range <claim> 0..100           -- numeric bounds, enforced at check time
  // @unit <claim> USD               -- expected unit for a numeric claim

@effect, @effect-default and @consistency are NOT recognised. A ruleset
carrying one does not compile.

Worked example:

  version 1.3;
  // @ask loanAmount What is the loan principal, in USD?
  // @unit loanAmount USD
  // @range loanAmount 0..100000000
  declare loanAmount as numeric;
  // @ask hasAppraisal Does the file contain a completed appraisal?
  declare hasAppraisal as boolean;
  declare riskTier as enum ["low","medium","high"];

  assert within_cap { loanAmount <= 1000000 "USD" };
  assert large_but_documented { Or(loanAmount <= 500000 "USD", hasAppraisal) };
  assert appraisal_when_large {
    IF loanAmount > 250000 "USD" THEN hasAppraisal ELSE True END
  };
  assert tier_permitted { riskTier != "high" };

Integrity rule: unknown is a first-class answer. If you cannot determine a claim's value from the evidence, submit the string "unknown" (or JSON null). Never guess, never substitute a type-correct placeholder to satisfy a validator, and never omit the claim. A guessed value produces a confident TRUE or FALSE about a situation that does not exist, which is worse than no answer; "unknown" produces an honest UNKNOWN.

<!-- generated by dsail 1.0.2; wire contract 2.8.0; re-run `dsail init` to refresh -->
