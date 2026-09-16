# Contributing to `dsail`

**This repository is a read-only mirror, and pull requests opened here cannot
be merged.** That is a property of how it is published, not a judgement about
your change. Please read the next section before spending time on a patch.

## Why a mirror

`dsail` is developed in Jaxon's private source repository, which is the single
authoritative copy. This repository and the `dsail` package on PyPI are both
produced from it automatically: every commit here carries a `Source-Commit`
trailer naming the commit it came from, and a scheduled check fails loudly if
the two ever stop matching.

A commit that exists here and not in the source repository is therefore a
defect that the next mirror run erases. There is nowhere for a merge to land.
We would rather say that plainly than leave a pull request open for months.

## What we do want

**Issues are open and we read them.** They are the supported way to reach the
people who maintain this package, and the route back into the source repository
runs through them:

* **A bug.** Open an issue with the `dsail` version (`dsail version`), your
  Python version, what you ran and what came back. If the service returned a
  structured error, include its code and the documentation URL it carried.
* **A patch you have already written.** Open an issue, describe the change, and
  paste the diff or link a branch on your fork. We will apply it in the source
  repository with attribution in the commit and the release notes. This is
  slower than a merge button and it is the only route that does not break the
  guarantee that PyPI and this repository are the same bytes.
* **A missing capability, or copy that misleads.** Open an issue. The README's
  opening section in particular is measured rather than written, so a report
  that it gave you the wrong idea is genuinely useful.
* **A security report.** Do not open an issue. See [SECURITY.md](SECURITY.md).

## What belongs somewhere else

This package is a REST client, a stdio MCP proxy, a local review UI and a
scaffolding command. It holds **no parser, no compiler and no solver** — the
DSAIL language, its compilation and the solver all run on the hosted service.
A change to how a policy compiles, to what a rule means, or to an evaluation
result is a change to the service and cannot be made here. Open an issue and
say what the service got wrong; it will reach the right people.

Questions about using DSAIL, rather than about this package, are better served
by the documentation at <https://docs.agents.jaxon.ai>, which is written for
agents and serves every page as markdown.

## Licence and attribution

The client is Apache-2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)). A patch
contributed through an issue is taken under that licence. Contributors are named
in the commit that lands the change and in the release notes for the version
that carries it.
