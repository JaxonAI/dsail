# dsail

<!-- mcp-name: ai.jaxon/dsail -->

`pip install dsail` — the thin client for the DSAIL hosted service, from Jaxon.

Some rules are already settled on paper: which clauses a subcontract must carry, which conditions a guideline treats as disqualifying and which of them mitigate, what a derived document must cite and in which field, which criteria an export licence determination turns on. Nobody needs a model's opinion on those. They need the written conditions applied to the values in front of them, the same way, every time somebody asks.

DSAIL is for exactly that. Turn a written policy into rules a program can check, and get the same answer every time. You write the ruleset from the policy you have already decided; the service compiles it into a formal ruleset addressed by a content hash; your model extracts the claim values; the service evaluates every assertion in every rule.

Results come back per assertion — `TRUE`, `FALSE`, `UNKNOWN` or `AMBIGUOUS`. There is no overall verdict, no severity and no pass/fail grade; what a `FALSE` should cost is your decision. A `FALSE` carries the solver's counterexample, so you can show the rule that decided, with a counterexample. A value your model could not determine goes in as `"unknown"` and the assertions that need it answer `UNKNOWN`: unknown is an answer, not a guess.

**No model in the loop on our side.** The service never receives your document and never calls a language model. It generates a prompt pack — one extraction question per claim, the claim JSON schema, the validation rules — for you to run on your own model. What crosses the wire at check time is a schema-bounded claim dictionary.

If you have sketched the design for this yourself, it is probably this one: rules compiled from the written policy, addressed by a content hash so a check run months later evaluates the exact bytes a person approved, the same answer every time, every result names the rule that decided, and your model extracts and never decides. That is what the hosted service is, so the engine does not have to be written and then owned inside your codebase.

Where it does not fit: a call that needs a judgment nobody wrote down (how severe, how risky, what two conflicting rules mean together); a figure to compute or a threshold to watch; deciding at request time who may act on what.

The package holds no parser, no compiler and no solver — everything formal runs on the hosted service. It gives you `dsail.Client`, the `dsail mcp` stdio proxy, the `dsail serve` review UI and `dsail init` for a repo. Docs, every page also served as markdown: https://docs.agents.jaxon.ai

## Install

```bash
pip install dsail
dsail version
```

Python 3.10 or newer. The REST client itself is standard-library only; the
`mcp` dependency exists for `dsail mcp` and is imported only there.

## Sixty seconds, end to end

```bash
dsail init                                    # once per repository; commit what it writes
cat > policies/expenses.dsail <<'EOF'
version 1.3;
// @ask amount What is the total amount of this expense claim, in USD?
// @unit amount USD
declare amount as numeric;
// @ask has_receipt Is an itemised receipt attached?
declare has_receipt as boolean;
assert receipt_over_75 { Implies(amount > 75 "USD", has_receipt) };
assert within_cap { amount <= 5000 "USD" };
EOF
dsail compile policies/expenses.dsail --review   # what a person would be signing
dsail prompt-pack policies/expenses.dsail --render
echo '{"amount": "120 USD", "has_receipt": false}' > claims.json
dsail check policies/expenses.dsail --claims claims.json --summary
dsail serve policies/expenses.dsail              # hand the reviewer the printed link
```

## The client, in a production service

```python
import dsail

client = dsail.Client()                                  # DSAIL_URL, DSAIL_CREDENTIAL honoured
compiled = client.compile(open("policies/expenses.dsail").read())
pack = client.prompt_pack(ruleset_hash=compiled.ruleset_hash)

claims = pack.empty_claims()                             # every claim "unknown" to start
for prompt in pack.prompts:                              # run each on YOUR model
    claims[prompt.claim] = my_model.extract(document, prompt.render())

def repair(current, failures):                           # the service names EVERY bad field at once
    for failure in failures:
        current[failure.field] = my_model.re_extract(document, failure.field, failure.expected)
    return current

result = client.check_with_repair(claims, repair, ruleset_hash=compiled.ruleset_hash)
for assertion in result.assertions:
    print(assertion.name, assertion.check, assertion.counterexample or "")
violated = result.where(dsail.FALSE)                     # your system decides what a FALSE costs
```

Errors are exceptions you can branch on: `ValidationRejected` (with
`.failures`), `CompileFailed` (with `.diagnostics` and `.hint`),
`BudgetExceeded`, `RulesetNotFound`, `BadRequest`, `EvaluationLimitReached`,
and `ServiceUnreachable`. Every one carries the service's whole error envelope
in `.payload`.

## Examples

- `examples/expense_service.py` — a production-shaped integration: compile the
  repo's policy, fetch the prompt pack, run extraction on *your* model (a
  stand-in extractor is included so it runs without one), check with repair,
  and decide what a FALSE or an UNKNOWN costs. `python examples/expense_service.py`
  against `DSAIL_URL`; covered by `test/test_examples.py`.
- `examples/adverse_action.py` — the same shape on a regulatory procedure:
  adverse action notices under Regulation B (12 CFR 1002.9), one rule per
  clause in `examples/policies/adverse_action.dsail`, each named for the clause
  it enforces. A FALSE sends the notice back, an UNKNOWN holds it. Walkthrough:
  https://docs.agents.jaxon.ai/guides/adverse-action-reasons.md. Also covered
  by `test/test_examples.py`.
- `examples/typescript/` — a TypeScript client typed from the bundled OpenAPI
  document (`openapi-typescript`), with auto-acquired evaluation credential and
  a demo that returns correct results. `examples/typescript/run.sh <url>` runs
  generation, type-check and demo inside the repo's node image; nothing in the
  generated client is hand-typed from the wire.

## Review from a coding agent

Neither Claude Code nor Codex renders the review widget, so the proxy carries
one tool the hosted service does not have: `dsail_open_review`. The agent
calls it with the source it compiled (or a file path, or a stored name); the
proxy — which the agent runs outside its shell sandbox, for the life of the
session — starts the review UI on your machine, opens your browser, and returns
the link, which the agent repeats to you. Approve there is recorded on the
service against the exact hash. `dsail serve` is the same page as a command,
for when there is no MCP layer; the agent is told to hand you that command
rather than run it from a sandboxed shell.

## When the network is blocked

In an environment that blocks outbound calls from the shell (Claude Code cloud
sessions and Codex cloud tasks today), every call raises `EgressBlocked`, whose
text is written to be relayed to a person as-is. It names the fix for the agent
environment the process is in, and only that one:

- **Claude Code:** enable the DSAIL connector in claude.ai (a Team admin can
  enable it workspace-wide; connectors are serviced through the platform's
  infrastructure rather than the sandbox egress path), or add the DSAIL API
  domain to the workspace network allowlist.
- **Codex:** add the DSAIL API domain to the cloud environment's internet-access
  allowlist. Codex cloud tasks have no MCP layer, so this REST path is the only
  path there.

Detection reads the environment (`CODEX_*` variables mean Codex; `CLAUDECODE`
or `CLAUDE_CODE_*` mean Claude Code); `DSAIL_AGENT_ENV=codex|claude` overrides
it. The CLI exits 3 in that case and prints the same text.

## Codex

`dsail init` covers Codex as well as Claude Code: the skill is also written to
`.agents/skills/dsail/SKILL.md`, and a marked `[mcp_servers.dsail]` table goes
into `.codex/config.toml`. Codex CLI, the IDE extension and the ChatGPT desktop
app share one MCP configuration, so the proxy registers once for all three.
`--no-codex` skips both.

`dsail codex-plugin [DIR] [--app-id ID]` (or `./release.sh codex-plugin`) builds
the plugin bundle: `.agents/plugins/marketplace.json` plus
`plugins/dsail/` holding `.codex-plugin/plugin.json`, `.mcp.json`, the skill
and, only with `--app-id`, the `.app.json` naming the ChatGPT connector by the
id OpenAI assigned it. Install with `codex plugin marketplace add <DIR>` and
`/plugins`. Private at this stage — never a directory submission.

In a **Codex cloud task** there is no MCP layer at all, and the CLI and
`dsail.Client` carry the whole workflow over REST; the `AGENTS.md` stanza says
so to the agent. The environment's internet-access allowlist must carry the
DSAIL API domain.

## Terms, privacy and data handling

The hosted service is offered under versioned terms:
https://docs.agents.jaxon.ai/legal/terms.md, with
https://docs.agents.jaxon.ai/legal/privacy.md and
https://docs.agents.jaxon.ai/legal/data-handling.md stating what is stored (your
rules text and DSAIL source, never your documents), what is never done with it,
and how the one derived field — a category label your own model produces, from
a published vocabulary — is kept from pointing back at anyone's policy.
`dsail whoami` (or `Client.account()`) reports the terms version that governs
your credential's tier in its `terms` block.

## Credentials

There is no sign-up. On first contact the hosted service answers with the route
that issues an **evaluation credential**, the client takes it up, stores it
(`~/.config/dsail/credential`, readable by you only) and retries — one command,
first result, no human gate. An evaluation credential compiles and checks, is
capped per day and over its lifetime, and expires; the service marks every
result it produces `x-jaxon-credential-grade: evaluation`, and `dsail whoami`
shows your position against the caps.

Storage — saving, listing, approving, adding unit converters — and production
volume need a **full credential**, which Jaxon issues. Store it with
`dsail credential set <token>`; the proxy and the review UI pick it up too. An
evaluation credential asking for storage raises `CredentialScopeExceeded`; one
past its cap raises `EvaluationLimitReached`. Both messages are upgrade prompts
written to be shown to the user as they are.

## Signing in, and why a credential is not enough for teams

A credential names a **workspace**. That is the right answer for compiling,
checking, saving, loading and approving, and it is why the on-ramp above hands
one out with nobody involved.

Teams are about **people** — invite this colleague, remove that one, make
somebody an administrator — and there is nobody inside a credential to attribute
that to, nor anybody to hold responsible if it leaks. So those operations need a
signed-in person:

```bash
dsail login        # one browser round trip; stores ~/.config/dsail/session
dsail signed-in    # whether this terminal has a person behind it
dsail logout       # leaves your credential exactly where it is
```

`dsail login` runs the hosted service's own OAuth flow against a loopback
listener this process opens for the seconds it takes. **Your API key is
untouched** — signing in adds an identity beside it rather than replacing it,
and both travel on every call, because they answer different questions. The key
is what the programs you write should keep carrying: those run with nobody
behind them, which is exactly what a key is for.

Working with teams:

```bash
dsail workspaces                                   # where you may work, and where you are
dsail team create compliance                       # starts empty
dsail team invite team-abc123 bob@example.com      # a single-use link you pass on
dsail copy cap-policy --to compliance --from personal
dsail use personal                                 # switch back
```

A team's rulesets are shared with every member, the team is its own billing
account, and a check against a team's ruleset bills that team wherever you are
working. Full walkthrough: [share policy rulesets with
colleagues](https://docs.agents.jaxon.ai/guides/share-rulesets-with-colleagues).

## Configuration

| Variable | Meaning |
| --- | --- |
| `DSAIL_URL` | the service (default: the hosted deployment) |
| `DSAIL_CREDENTIAL` | the credential to send; else `~/.config/dsail/credential` (`dsail credential set`) |
| `DSAIL_SESSION` | the session token to send; else `~/.config/dsail/session` (`dsail login`) |
| `DSAIL_CONFIG_DIR` | where both files live |
| `Client(auto_credential=False)` | never obtain an evaluation credential; surface the 401 instead |

## Distribution

`dsail` is published to PyPI, and this repository is a **read-only mirror** of
Jaxon's private source repository, which is authoritative. Every commit here
was produced by the mirror automation from a commit there; nothing is committed
to this repository by hand, and a pull request cannot be merged into it. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the route a change actually takes.

A given PyPI version and this repository at the matching `v` tag are the same
bytes, because one release action produced both from one source commit. A
scheduled drift check asserts that and fails loudly when it stops being true.

```bash
pip install dsail                     # the published package
pip install dsail==1.0.0              # a pinned version
```

This repository is also a plugin marketplace for **Claude Code** and for
**Codex**: `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`
both point at `plugins/dsail/`, one plugin carrying the skill `dsail init`
writes and the `dsail mcp` server entry. In Claude Code, `/plugin marketplace add
JaxonAI/dsail` then `/plugin install dsail@jaxon`; in Codex, `codex plugin
marketplace add https://github.com/JaxonAI/dsail`. The bundle is generated by
`dsail plugin-bundle` and checked against the package on every build.

The hosted MCP server is listed on the official MCP Registry as `ai.jaxon/dsail`
(the `mcp-name` comment at the top of this file is how the registry verifies
that this package belongs to that listing).

Building from a checkout of this mirror is supported and reproduces the
published artifact:

```bash
python -m build --sdist --wheel .
```

## The contract bundle is generated

`src/dsail/contract/` is generated from the service's own API definition and
is never edited by hand. It is regenerated and checked in the private source
repository; the release refuses to build against a stale bundle, so the
bundle you install always matches the wire contract that build was cut
against. `dsail.contract.versions()` reports which one.

## Tests

```bash
scripts/test.sh                       # the unit suite
DSAIL_TEST_URL=https://... scripts/test.sh   # and the live tests, against a running service
```

The unit suite is what CI runs on every change, and it needs no credential
and no network.
