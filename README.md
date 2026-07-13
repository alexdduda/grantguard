# GrantGuard

A corruption-resistant algorithm for government grant and contract allocation. Built, stress-tested, and iteratively improved across six versions using mechanism design theory, Byzantine fault tolerance, and Monte Carlo simulation calibrated to real EU, US, and Canadian procurement data.

---

## What This Is

Government procurement loses an estimated 20-25% of spending to corruption annually, roughly $154B per year in the United States and CAD$7B in Canada. Standard approaches focus on detection after the fact. GrantGuard focuses on changing the incentive structure so corruption becomes economically irrational in the first place.

The system is stress-tested against 15 attack vectors, 7 based on empirically documented patterns from EU Structural Funds, FPDS-NG, and PSPC audit data, and 8 forward-looking scenarios including LLM-assisted proposal gaming, state-actor reviewer infiltration, and AI-induced score homogeneity.

The original V3–V6 design reported resistance to 12 of 15 attack vectors. A later independent adversarial audit (V7–V11, see [AUDIT_FINDINGS.md](AUDIT_FINDINGS.md)) found several of those "resistant" claims were overstated — the original tests largely graded their own homework. Under adaptive adversaries the detection layer turned out to be decorative, Krum was dominated by a plain median, and the surviving defenses (COI-dispersion, confidential rubric, per-entity caps, value-aggregation) all degrade to one binding dependency: **the quality of the identity/relationship graph** (COI links, beneficial ownership, entity resolution). That is an institutional/data asset, not an algorithm. Read the audit before relying on any single headline number here.

---

## Repository Structure

```
grantguard/
├── simulation/
│   ├── grantguard_simulation.py # V3 base: core data structures and pipeline
│   ├── grantguard_v4.py         # V4: two-layer rubric, LR rotation test, Krum
│   ├── grantguard_v5.py         # V5: future scenarios, US/Canada modules
│   ├── grantguard_v6.py         # V6: feedback loop, ML classifier, whistleblower, PQC
│   ├── grantguard_v7.py         # V7 AUDIT: decoupling, adaptive adversary, centroid-Krum
│   ├── grantguard_v8.py         # V8 HARDEN: median>krum, k=7, dispersion, confidential
│   ├── grantguard_v9.py         # V9 ATTACK: COI-coverage collapse, rubric leakage
│   ├── grantguard_v10.py        # V10 AXES: temporal/CUSUM, volume/cap, splitting/aggregation
│   ├── grantguard_v11.py        # V11: COI-coverage collapse reproduced on the REAL example graph
│   └── coi_graph.py             # REAL COI-graph interface: load from CSV, COI-aware assignment
├── data/
│   └── example_coi_graph/       # Concrete example graph (nodes.csv, edges.csv) + README
├── docs/
│   └── grantguard_docs.js       # Generates Word documents
├── articles/
│   ├── llm_gaming.md            # Article: how LLMs break procurement scoring
│   ├── whistleblower_gap.md     # Article: US FCA vs Canada PSDPA
│   └── grantguard_article.md    # Article: AI lab overview
├── HOW_IT_WORKS.md              # Plain-language mechanics: the two graphs, commit-reveal, the economics
├── AUDIT_FINDINGS.md            # Independent adversarial audit (V7–V11, incl. real graph) and verdict
├── requirements.txt
└── README.md
```

New here? Read [HOW_IT_WORKS.md](HOW_IT_WORKS.md) first — it explains the two
separate graphs (COI vs commit-reveal), who can see what, how the relationship
graph is actually sourced, and why the "economically irrational" claim is an
inequality (and where it breaks).

---

## Core Algorithm

Five-layer allocation pipeline:

**1. Structured anonymity + two-layer rubric.** 60% of scoring criteria are public. 40% are drawn from a rotating confidential pool and revealed only after submission closes. Reduces LLM-assisted gaming without eliminating transparency.

**2. COI-constrained reviewer assignment.** Conflict-of-interest graph extended to 2-3 network hops. Assignment solved as a constrained matching problem.

**3. Cryptographic commit-reveal.** Reviewers commit scores via SHAKE-256 hash before any scores are revealed. Prevents score coordination. Post-quantum ready with migration path to ML-DSA (NIST FIPS 204) by 2030.

**4. Krum aggregation + empirical CRS.** Krum raises the breakdown point from 16% to 29% for k=5 reviewers. Corruption Risk Scores thresholded against an empirical null distribution from verified-clean procurement cycles.

**5. Randomised softmax selection.** Temperature parameter drawn from U(3, 8) per cycle and revealed only post-selection. Prevents applicants from optimizing against a known selection function.

---

## V6 Modules

| Module | Function |
|---|---|
| M1 PostAwardFeedbackLoop | Tracks contract outcomes, updates EigenTrust reviewer reputation, generates ML training labels |
| M2 WhistleblowerModule | US FCA qui tam intake, Canada PSDPA routing, protection assessment, ground truth pipeline |
| M3 MLCorruptionClassifier | Logistic classifier on 12 CIM indicators plus interaction features, retrained on verified outcomes |
| M4 EconomicImpactModel | Cost-benefit projections for US and Canada across conservative/moderate/optimistic scenarios |
| M5 SubcontractorTransparency | Prime-to-sub related-entity detection, pass-through fraud flagging |
| M6 OTAMonitor | US Other Transaction Authority abuse detection (10 USC 4021-4022) |
| M7 StandingOfferMonitor | Canada PSPC call-up concentration analysis |
| M8 SmallBusinessFraudDetector | 8(a)/HUBZone/WOSB/SDVOSB/PSIB front-company and affiliation detection |
| M9 EmpiricalCRSCalibrator | Builds null CRS distribution from clean historical data, fixes false positive rate |
| M10 PostQuantumCryptoLayer | SHAKE-256 hybrid commit-reveal, NIST PQC migration pathway |

---

## Simulation Framework

Each attack scenario runs 80-150 Monte Carlo iterations. Four metric categories:

- **Quality efficiency:** ratio of selected proposal quality to omniscient optimum
- **Corruption penetration rate (CPR):** fraction of selected proposals that are corrupt beneficiaries
- **Corruption ROI:** expected return per unit of bribe/coordination cost (target: below 1.0)
- **Detection metrics:** sensitivity, specificity, false positive rate

---

## Theoretical Grounding

| Framework | Application |
|---|---|
| Byzantine Fault Tolerance (Lamport et al. 1982) | Reviewer = Byzantine node. Krum aggregation is the FL-derived solution. Minimum k=7 for high-value awards. |
| Bayesian Truth Serum (Prelec 2004) | Proper scoring rules make honest reporting a Nash equilibrium without requiring ground truth. |
| VCG Mechanism | Cost dimension scored via transfer payments that penalize overbidding. Implemented in Canada module. |
| EigenTrust (Kamvar et al. 2003) | Reviewer reputation computed as principal eigenvector of accuracy-weighted trust matrix. |
| Robust Statistics | Krum's influence function is bounded where the trimmed mean's is not. |

---

## Attack Vector Results

| Scenario | V3 | V5 | V6 |
|---|---|---|---|
| Sparse bribery | Resistant | Resistant | Resistant |
| Reviewer collusion | Resistant | Resistant | Resistant |
| Specification gaming | Failed (CPR 0.47) | Partial (CPR 0.44) | Partial (CPR 0.44) |
| Bid rotation | Partial (CPR 0.11) | Partial (CPR 0.11) | Improved |
| Sybil / identity splitting | Resistant | Resistant | Resistant |
| Administrative capture | Resistant | Resistant | Resistant |
| False input data | Resistant | Resistant | Resistant |
| LLM gaming (C8) | Not modeled | Monitored | Monitored |
| State actor infiltration (C12) | Not modeled | Partial | Monitored |
| Democratic capture (C15) | Not modeled | Not modeled | Flagged (post-award only) |

---

## Economic Case

US moderate scenario over 10 years:
- Annual savings: $27.7B
- Deployment and operating cost: $377M
- Net: $262B
- Breakeven: Year 1

Canada moderate scenario over 10 years:
- Annual savings: CAD$1.08B
- Deployment and operating cost: CAD$86M
- Net: CAD$10.1B
- Breakeven: Year 1

---

## Installation

```bash
git clone https://github.com/yourusername/grantguard.git
cd grantguard
pip install -r requirements.txt
```

Run the base simulation:

```bash
python simulation/grantguard_simulation.py
```

Run V6 with all modules:

```bash
python simulation/grantguard_v6.py
```

Generate documentation (requires Node.js):

```bash
npm install docx
node docs/grantguard_docs.js
```

---

## Residual Vulnerabilities

The adversarial audit (V7–V11) materially revised this list. See [AUDIT_FINDINGS.md](AUDIT_FINDINGS.md) for the full numbers.

**Entity/relationship-data dependency (the binding constraint).** Across six independent attack vectors, every surviving defense — COI-dispersion assignment, per-entity submission caps, related-award value aggregation — degrades in proportion to how incomplete the identity/relationship graph is. With realistic COI coverage (~0.25–0.5), a co-assigned ring of 4–5 funds a mediocre proposal 30–90% of the time. This is a data/institutional limit, not an algorithmic one.

**Specification / LLM gaming.** The two-layer confidential rubric mitigates but does not resist gaming, and an *adaptive* gamer evades the uniformity detector (recall collapses ~86%→35%). The confidential rubric also decays toward useless as its dimension pool leaks across rounds, so it requires continuous rotation.

**Coordinated collusion under thin oversight.** A reviewer ring that meets/exceeds the Byzantine tolerance `f` defeats robust aggregation outright; a majority ring defeats every aggregator tested. Detection (CIM/CRS) was found to be effectively decorative — decoupling it changed corruption penetration by <0.3% — and Krum specifically was dominated by a plain median on both robustness and clean-data quality.

**Democratic / institutional capture.** Reviewers favoring politically connected applicants is detectable only in retrospect; full institutional capture survives no within-system mechanism. Unchanged from the original assessment.

---

## References

- Fazekas, M. & Kocsis, G. (2020). Uncovering High-Level Corruption. *British Journal of Political Science.*
- Blanchard, P. et al. (2017). Machine Learning with Adversaries: Byzantine Tolerant Gradient Descent. *NeurIPS.*
- Prelec, D. (2004). A Bayesian Truth Serum for Subjective Data. *Science.*
- Kamvar, S. et al. (2003). The EigenTrust Algorithm for Reputation Management in P2P Networks. *WWW.*
- Lamport, L., Shostak, R. & Pease, M. (1982). The Byzantine Generals Problem. *ACM TOPLAS.*
- OECD (2016). Preventing Corruption in Public Procurement.
- US GAO (2023). Federal Procurement: Actions Needed to Improve the Tracking of Sole-Source Contracts.
- Office of the Auditor General of Canada (2021). Report 6: Phoenix Pay System.

---

## License

MIT
