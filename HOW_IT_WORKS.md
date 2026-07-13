# How GrantGuard Works (and where the explanation usually goes wrong)

This document explains the mechanics in plain terms, including two things that are
easy to conflate and that earlier drafts of the docs blurred together. Read
[AUDIT_FINDINGS.md](AUDIT_FINDINGS.md) alongside it — this file describes the
*design intent*; the audit describes how much of it actually holds up.

---

## What "attack" means here

An algorithm is a fixed rule: **inputs → output.** An *attack* is a strategy
someone with a stake in the output uses to make the rule produce a decision it
shouldn't — here, **funding the wrong applicant.** Concretely, every attack is a
manipulation of the **scores** that go in:

- a bribed reviewer adds a boost to a favoured proposal,
- a *ring* of reviewers pre-agrees to post the same inflated number,
- an applicant inflates the *observable* features reviewers can see (gaming).

"Resistant to an attack" means: run the algorithm with that manipulation present,
and it *still* funds roughly the right people. We measure this with **CPR
(corruption penetration rate)** — the fraction of funded winners who are the
corrupt ones. Attack succeeds → CPR high; algorithm resists → CPR near zero.

---

## The one thing to get straight: there are TWO graphs / two anonymities

Earlier explanations (including ours) lumped these together. They are different:

| | **COI graph** (used at *assignment*) | **Commit-reveal** (used at *scoring*) |
|---|---|---|
| Question | "Who is *connected* to whom?" | "Can reviewers *see each other's scores* while scoring?" |
| Purpose | Don't let a reviewer judge someone they're tied to | Stop reviewers matching numbers in real time |
| Made of | social / professional relationships | cryptographic hashes |

And two different anonymities:

- **Applicants are anonymised _to reviewers_** ("structured anonymity"): a reviewer
  scores a proposal without being told whose it is. This fights favouritism.
- **Reviewers are _not_ anonymous to the system.** The system must know reviewer
  identities to run COI checks and risk scoring.
- **Reviewers may well know _each other_** — the design assumes they might (experts
  in a small field). It does not try to make them strangers.

So it is **not an anonymous committee.** It is a *known* committee scoring
*anonymised* proposals.

---

## Where does the relationship (COI) graph come from?

**In this codebase: it is synthetic.** `generate_network()` sprinkles random edges.
Fine for a simulation — but it is the biggest gap between the sim and reality.

**In a real deployment you would have to _build_ the graph** from data sources, none
of which are complete:

- government HR / employment history (did a reviewer work at the applicant's firm?),
- corporate registries + beneficial ownership (who really owns the applicant?),
- co-authorship / publication / patent databases (academic ties),
- board memberships and directorships,
- past contract-award records,
- self-disclosure forms (which people omit or lie on).

Merging and de-duplicating those into "person X is linked to person Y" is the
**entity-resolution problem.** The relationships that exist in reality but appear
in *none* of those databases — an off-record friendship, a cousin, a quiet side
deal — are the **hidden ties** the algorithm cannot route around. It can only avoid
a conflict it can *see*.

**See this for yourself:** a concrete example graph lives in
[`data/example_coi_graph/`](data/example_coi_graph/) and a real loader/interface in
[`simulation/coi_graph.py`](simulation/coi_graph.py). Run `python
simulation/coi_graph.py` and it shows the same graph under two data-coverage
regimes: self-disclosure alone catches 6 of 8 real conflicts; adding external
registries catches 7 of 8; and an undisclosed sibling tie between a reviewer and
an applicant is caught by *neither* — so the assignment step happily seats that
reviewer on their own sibling's panel and never flags it. That one invisible edge
is this whole document's argument in miniature. `coi_graph.assign_with_coi()` is
the interface a real deployment implements in place of the synthetic
`generate_network()`.

---

## What commit-reveal does and does not stop

Each reviewer first submits `hash(score + secret_salt)`. A hash cannot be run
backwards, so **nobody — not other reviewers, not the system — learns any score
yet.** Only after *all* commitments are locked in does everyone reveal their real
number, and the system checks each matches its hash. Because your score was frozen
before you saw anyone else's:

- **Stops:** live, in-system matching — "let me see your score, then I'll match it."
- **Does NOT stop:** reviewers who agree *offline beforehand* ("we all score Firm X
  a 9"). They each simply commit a 9. Commit-reveal is irrelevant to them.

That pre-arranged group is the **collusion ring**, and it is exactly what defeated
the robust aggregator in the audit. So commit-reveal raises the bar from *trivial
live coordination* to *must pre-arrange offline and trust each other* — a real but
**modest** increase, and near-useless against a committed ring.

---

## Why corrupting it is *meant* to be uneconomic

The claim is **not** "corruption is impossible." It is an inequality:

> **Expected gain from corrupting  <  Expected cost of corrupting**

The algorithm cannot change the gain (the contract is worth what it is worth), so
every mechanism works on the **cost** side — by forcing you to corrupt *more people*
to move the outcome:

| mechanism | what it forces on the attacker |
|---|---|
| **Robust aggregation** (median) | one bribed score is thrown out as an outlier → bribing *one* reviewer buys nothing; you must flip a **majority of k**. |
| **k = 7 reviewers** | "majority" now means bribing **4 people**, not 1 — 4× the money and 4× the chance someone talks. |
| **Randomised selection** | even a top score only *probabilistically* wins, so the payoff isn't guaranteed even after you pay. |
| **Commit-reveal** | colluders can't confirm each other through the system, so rings must be pre-arranged and are less stable. |

The intent: to steer a $10M contract you'd have to bribe 4+ people, each adding cost
and detection risk, until total expected cost exceeds the gain — at which point a
rational corrupt actor **doesn't bother.** The code even computes
`corruption_roi = contract_value / bribe_cost` and treats the system as "working"
only when that ratio is below 1.

---

## The honest caveat

That inequality is the **design intent, not a proven property.** The audit
(V7–V10) found it does not hold in the shipped configuration:

- The robust aggregator (Krum) let a ring of **2** — not a majority — flip the
  outcome, so "you must bribe a majority" was false.
- The detection layer added no real cost or risk (it was decorative).
- Whether an attacker can even assemble a cheap ring depends entirely on whether
  the COI graph exposes their links — the **hidden ties** it usually can't see.

Everything collapses to one root: the system is strong against *opportunistic,
in-system* cheating and weak against *pre-arranged, off-record* cheating — and
telling those two apart requires knowing the real relationship graph, which no
database fully has. **The ceiling is data quality, not the algorithm.** See
[AUDIT_FINDINGS.md](AUDIT_FINDINGS.md) for the numbers.
