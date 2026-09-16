# Security policy

## Reporting a vulnerability

Email **security@jaxon.ai** with the details. Do not open a public issue, and
do not open a pull request against this repository — it is a read-only mirror
and a patch here would be public before it was fixed.

Please include what you found, how to reproduce it, the `dsail` version
(`dsail version`) and the Python version. If a proof of concept is short,
include it; if it is not, describe the shape rather than attaching a working
exploit.

We will acknowledge a report within three working days and tell you what we
intend to do about it. If we ship a fix, the release notes credit you unless
you ask us not to.

## Scope

This repository is the **client**: a REST client, the `dsail mcp` stdio proxy,
the `dsail serve` local review UI and `dsail init`. In scope here:

* anything that leaks a credential, writes one to disk in the clear, or sends
  one somewhere other than the configured service host;
* anything that lets a policy file, a claims file or a service response cause
  code execution, path traversal or an unintended write in the caller's
  environment;
* the local review UI (`dsail serve`) binding wider than localhost, or serving
  content it should not;
* a supply-chain problem in what the published wheel actually contains.

The hosted service at `agents.jaxon.ai` is also in scope, through the same
address, but it is a different codebase and this repository will not help you
read it.

## What the client does with your data

Stated here because it bounds what a vulnerability in this package can expose.
The client never sends your document anywhere. Extraction runs on **your**
model; what crosses the wire at check time is a schema-bounded claim dictionary
and the ruleset. The service never calls a language model. Credentials are read
from `DSAIL_CREDENTIAL` or `~/.config/dsail/credential` and are sent only to the
configured service host.

## Supported versions

Fixes land on the latest published version. There are no long-term support
branches; upgrade to the current release.
