---
name: dsail-regulations-and-engineering-standards
description: "Apply written regulations and engineering standards consistently: FAR and DFARS clause flowdown, NAICS size standards, state regulations, FOIA and public records requests, derivative classification guidance, security clearance adjudication guidelines, per diem rates, reliability policy, incident severity classification, on-call policy, change management and change freeze policy. Each determination names the provision or requirement that decided."
---

# Regulations and engineering standards with DSAIL

Written regulations and internal standards applied one case at a time: a
subcontract against FAR and DFARS flowdown clauses, a firm against NAICS size
standards, a records request against FOIA exemptions, a document against a
classification guide, a clearance case against adjudication guidelines, a
voucher against per diem rates, an incident against a severity matrix, a change
or a release against a written standard. DSAIL turns the written conditions
into a ruleset and names, for every case, the provision or requirement that
decided.

The `dsail` skill has the authoring sequence, the grammar and the integrity
rule. Follow it. This skill is about how these rules map onto it.

## Before writing any DSAIL

- Restate each provision in plain English with its citation (clause number,
  section, matrix row) and get the person's confirmation. The citation goes into
  the rule's description comment.
- DSAIL applies what the text states. Where a regulation or standard leaves the
  decision to an official (a whole-person judgement, a waiver, an "as
  appropriate"), that part stays with them.

## How these rules usually look

- **Clause flowdown**: the subcontract's attributes are claims (value in USD,
  contract type as an enum, commercial item as a boolean), plus one boolean per
  clause present. One assert per clause, so a FALSE names the missing clause.
- **Size standards and thresholds by code**: declare the code as an unordered
  enum of the codes the policy lists and branch with `CASE`; receipts carry a
  unit, headcount is a plain number.
- **Exemptions and release decisions**: one boolean per exemption, answered by
  extraction and "unknown" where the record is not clear. DSAIL applies what
  the rule says follows; it does not judge whether a passage is sensitive.
- **Classification and severity levels**: an ordered enum compares the level
  applied with the level the guide or matrix requires.
- **Rates and entitlements**: the rate for the place and dates is a claim (or
  the policy's own table, written as a `CASE`); compare the amount claimed with
  units on every literal.
- **Engineering standards** (release, change freeze, on-call, reliability
  policy, licence policy): the facts come from the change record, the incident,
  the dependency list or the release ticket, and each written requirement is
  one assert. DSAIL does not scan code, read configuration or compute figures
  such as an error budget; the system that has the facts supplies them.

## Example

```
version 1.3;
// @ask touchesPaymentPaths Does the change touch payment code paths?
declare touchesPaymentPaths as boolean;
// @ask approverCount How many people approved the release?
// @range approverCount 0..50
declare approverCount as numeric;
// @ask paymentsApprover Is at least one approver from the payments team?
declare paymentsApprover as boolean;
// @ask rollbackTested Has the rollback plan been tested?
declare rollbackTested as boolean;
// @ask subcontractValue What is the total value of the subcontract, in USD?
// @unit subcontractValue USD
// @range subcontractValue 0..10000000000
declare subcontractValue as numeric;
// @ask includesSmallBusinessPlanClause Does the subcontract include the small business subcontracting plan clause?
declare includesSmallBusinessPlanClause as boolean;
// @ask requiredLevel What classification level does the guide require for this content: unclassified, confidential, secret or top secret?
declare requiredLevel as enum ["unclassified","confidential","secret","top_secret"];
// @ask markedLevel What classification level is the document marked at: unclassified, confidential, secret or top secret?
declare markedLevel as enum ["unclassified","confidential","secret","top_secret"];

// Standard 2.1: every release needs at least one approver.
assert has_approver { approverCount >= 1 };
// Standard 3.1: a change to payment paths needs a second approver, and one from payments.
assert payments_second_approver { IF touchesPaymentPaths THEN And(approverCount >= 2, paymentsApprover) ELSE True END };
// Standard 3.2: a change to payment paths needs a tested rollback plan.
assert payments_rollback_tested { Implies(touchesPaymentPaths, rollbackTested) };
// Flowdown 4: a subcontract over 750,000 USD must carry the small business subcontracting plan clause.
assert small_business_plan_flowdown { IF subcontractValue > 750000 "USD" THEN includesSmallBusinessPlanClause ELSE True END };
// Guide 1.3: a document is marked at least at the level the guide requires for its content.
assert marked_at_required_level { markedLevel >= requiredLevel };
```

The section numbers and amounts are illustrative; use the regulation's or the
standard's own.

## Checking, and keeping the record

- Extract claim values with the prompt pack on the user's own model; a fact the
  record does not state is "unknown", never a guess.
- Save the ruleset and record the accountable person's approval against its
  hash; a later edit is a new revision and needs its own approval.

## Not this skill

Deciding who may deploy, access or approve (that is access control), scanning
code or configuration, computing figures, and interpreting an ambiguous
regulation or drafting the standard.

<!-- generated by dsail 1.0.4; wire contract 2.9.0; re-run `dsail plugin-bundle` to refresh -->
