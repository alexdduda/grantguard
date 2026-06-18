# GrantGuard — Independent Adversarial Audit (V7–V10)

*An attempt to break GrantGuard's own results, harden it, and break the hardening.*

The original V3–V6 work reported resistance to 12 of 15 attack vectors. But those
tests were written by the same hand as the defenses, scored against labels the
generator itself planted, with attack parameters chosen by the author — i.e. the
algorithm largely **graded its own homework**. V7–V10 apply independent pressure:
adaptive adversaries, confound-free harnesses, and attacks aimed squarely at the
defenses' load-bearing assumptions.

All audits are reproducible from `simulation/grantguard_v7.py … v10.py`.
Numbers below are Monte-Carlo means (40–300 iterations/cell depending on the test).

---

## TL;DR — the one finding that matters

> **GrantGuard's ceiling is not its math. It is the quality of the identity /
> relationship graph it runs on** — conflict-of-interest links, beneficial
> ownership, entity resolution. Six independent attack vectors all reduce to this
> single dependency. The only purely-algorithmic win in the entire audit was a
> CUSUM trend test against slow score-creep, and even that is partial.

Secondary headline: the entire **detection layer (CIM / CRS / flags) is
decorative** as wired, and **Krum — the flagship aggregator — is strictly
dominated by a plain median.**

---

## V7 — Three diagnostics

### #3 Detection is decoupled from resistance
Removing the only coupling actually wired into selection (CRS down-weighting)
changes corruption penetration by **0.2%**. Forcibly coupling the flags to
selection doesn't help either — they don't point at the real corrupt actors.

| coupling mode | mean CPR | Δ vs baseline |
|---|---|---|
| baseline (as shipped) | 0.120 | — |
| no CRS weighting | 0.118 | −0.002 |
| drop high-CRS reviewers | 0.120 | 0.000 |
| exclude flagged applicants | 0.123 | +0.002 |

Per-flag: `variance_collapse`, `high_crs_reviewer`, `network_cluster`,
`ai_gaming_detected`, `synthetic_identity_detected` all register as **decorative**
(firing does not lower CPR). Whatever resistance exists comes from
Krum + randomized softmax + the rubric, not from detection.

### #1 The adaptive gamer evades the uniformity detector
By injecting calibrated cross-dimension variance to sit just above the `std<0.15`
filter, an adaptive LLM gamer cuts the detector's recall **86.4% → 35.3%** while
its **funding rate rises** (62.6% → 64.4%). The fingerprint in `llm_gaming.md`
is evadable at no cost to the attacker.

### #2 (clean) Centroid attack on Krum
Confound-free harness (full ring co-assigned, mediocre target, attack-off
counterfactual). A mediocre proposal that should never win:

| ring/k | Krum inflation | pure effect (funded that wouldn't be) | CRS catches |
|---|---|---|---|
| 2/5 | 0.674 | +23.3% | 1.7% |
| 3/5 | 2.000 | **+81.7%** | 5.0% |

Krum's "29% breakdown point" assumes the ring can't be co-assigned. Once it is,
the guarantee is void. CRS is near-blind to single-target collusion because its
correlation signal needs ≥2 co-reviewed proposals.

---

## V8 — Hardening (validated against the V7 attacks)

| defense | result |
|---|---|
| **Aggregator** | Plain **median dominates Krum** on *both* axes: lower attack success *and* higher clean-data quality efficiency (0.992 vs 0.985). Krum was the wrong tool. CARA (cluster-aware) ≈ median. |
| **k = 7** | Biggest single lever for minority rings: ring-2 success 24% → 8%. |
| **COI-dispersion** | Keeping the ring to ≤1 per panel drops success to ≤3% — *but only with a complete COI graph*. |
| **Confidential rubric (w≈0.4)** | Gamer win 78.5% → 63.4%, honest win 20% → 28%, quality efficiency 0.894 → 0.955. A free dampener — but not a cure. |

---

## V9 — Attacking the hardening

### A9 — COI-coverage collapse (median, k=7)
Dispersion is only as strong as the COI graph. Cartels recruit members with
**undisclosed** links. `pure effect` by ring size × COI coverage:

| ring | cov 1.0 | 0.75 | 0.50 | 0.25 | 0.0 |
|---|---|---|---|---|---|
| 3 | 2.0% | 6.7% | 10.7% | 14.0% | 22.7% |
| 4 | 0.7% | 6.0% | 6.7% | 30.7% | **90.0%** |
| 5 | 0.7% | 6.7% | 16.7% | 48.7% | **90.7%** |

The "≤3%" v8 result was the **coverage = 1.0 fantasy**. Realistic coverage
(~0.25–0.5) → 30–49% penetration. Undisclosed links → 90%.

### B9 — Confidential rubric is a depreciating asset
As the confidential dimension pool leaks across rounds, effective weight → 0 and
the gamer climbs back to undefended (61.5% → 78.3%). Requires continuous rotation.

### C9 — Combined worst case
Ring of 4, COI coverage 0.25, k=7, median: boost-only **32.7%**, boost + rival
suppression **42.0%**. Suppression attacks the denominator (drags genuine rivals
below the funding cutoff), which per-proposal-robust median cannot prevent.

---

## V10 — The three remaining axes (attack → targeted defense)

| axis | attack (no defense) | defense | residual |
|---|---|---|---|
| **Temporal creep** | 100% capture (by round 4) | CUSUM trend test | **37%** — only partial; the lone purely-algorithmic win |
| **Volume flooding** | up to 50% of slots | per-entity submission cap | ~8% (1 slot) — *but* defeated by shell entities (Sybil) |
| **Threshold-splitting** | 99.7% (≥1 of 4 k=3 splits) | related-award value-aggregation → k=7 | 7% — *but* requires entity linkage |

Two of three defenses (cap, aggregation) collapse to the **same entity-resolution
dependency** as v9's dispersion. CUSUM is the only one that doesn't — and it's
partial.

---

## Recommended configuration (honest)

Deploy, but with eyes open:

1. **Median aggregation** (drop Krum — it's dominated).
2. **k = 7** for contested/high-value awards.
3. **COI-dispersion assignment** — effectiveness gated on COI-graph coverage.
4. **Confidential rubric ≈ 0.4**, with a *mandatory rotation cadence*.
5. **CUSUM** cross-round trend monitoring on re-applying entities.
6. **Per-entity caps + related-award value-aggregation**, gated on entity resolution.
7. Treat the **CIM/CRS detection layer as un-validated** — do not couple it to
   selection until individual signals demonstrate CPR reduction.

## What no amount of code fixes

- Incomplete COI / beneficial-ownership / entity-resolution data (the binding constraint).
- Majority reviewer rings under thin oversight.
- A confidential rubric whose pool has leaked and isn't rotated.
- Democratic capture and full institutional capture (retrospective / out of scope).

The productive next investment is **not another algorithm** — it is the registry
data (COI graphs, beneficial-ownership, entity resolution) and the institutional
processes (rubric rotation, post-award audit, whistleblower recovery) that the
algorithms depend on.
