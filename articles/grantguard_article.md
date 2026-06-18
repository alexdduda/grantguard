# I Built an Algorithm to Fight Government Corruption - Then Spent Three Months Trying to Break It

*What mechanism design, Byzantine fault tolerance, and 80,000 Monte Carlo runs taught me about why procurement corruption is so hard to fix*

---

Government wastes roughly **$154 billion per year** in the United States alone, not through incompetence, but through corruption in procurement and grant allocation. The OECD puts the global figure at 20-25% of all procurement spending. That number is not an abstraction. It is hospitals that don't get built, infrastructure that fails, and research that doesn't happen because the contract went to the firm that paid the right person, not the firm that had the best proposal.

I spent the last several months building a system to fix this. More importantly, I spent most of that time trying to break it.

This is what I learned.

---

## The Problem Is Incentive Structure, Not Detection

The standard intuition about procurement corruption is that we need better auditing. Catch more corrupt actors, and corruption falls. This is wrong, and understanding why is the key to designing a system that actually works.

Consider the math a corrupt firm faces. A $10M contract, a bribe of $50,000 to a single reviewer, and a detection probability of maybe 5%, assuming the bribe works. Expected payoff: roughly $3-4M net. Expected cost: $50K bribe plus 5% chance of something bad happening, discounted by the fact that "something bad" usually means an investigation that takes years and results in a settlement.

No detection system can win that game if the underlying incentive structure stays intact. What you need is to raise coordination costs, reduce payoff certainty, and make the expected return on corruption fall below its cost. The goal is not to catch every corrupt actor. It is to make corruption economically irrational for the majority of cases.

This reframing changes everything about how you design the system.

---

## The Algorithm: Five Layers

GrantGuard is a five-layer allocation pipeline grounded in mechanism design theory. Here is what each layer does and why.

**Layer 1: Structured Anonymity + Two-Layer Rubric.** Removing names from proposals is the obvious starting point, but it is nearly impossible in practice. In specialized fields, writing style, cited methods, and institutional signals make de-anonymization trivial. So instead of blindness, the system implements structured anonymity: strip direct identifiers, then run NLP to detect indirect ones.

The more interesting innovation is the rubric split. Public criteria (60% of the score) are known to applicants before submission. Confidential criteria (40%) are drawn randomly from a pool and revealed only after submission closes. Applicants can optimize for the public portion but not the full score surface. This directly attacks specification gaming, where a procurement consulting industry emerges to write proposals that score maximally on the rubric without actually being good projects.

**Layer 2: COI-Constrained Reviewer Assignment.** Reviewers are assigned via a constrained matching algorithm that minimizes conflict-of-interest scores across all assignments simultaneously. The critical empirical finding here, from Fazekas & Kocsis's analysis of four million EU procurement contracts, is that corrupt reviewer-applicant relationships operate at 2-3 hops in the professional network, not direct connections. The COI graph must be extended to capture shared former employers, co-authors of co-authors, and intermediary law firms. Direct conflict-of-interest checks catch only naive corruption.

**Layer 3: Cryptographic Commit-Reveal.** Every reviewer commits their score cryptographically using SHAKE-256, a quantum-resistant hash function, before any scores are revealed. Only after all commitments are submitted are scores opened simultaneously. This eliminates the most common coordination mechanism: "I'll score it 8 if you score it 8." The protocol is also post-quantum ready; the signature layer migrates to ML-DSA (NIST FIPS 204) by the 2030 deadline.

**Layer 4: Krum Aggregation + Empirical CRS.** The standard approach, trimmed mean or median, has a breakdown point of 16-50%. A corrupt reviewer ring exceeding that threshold breaks the aggregation. We replace it with Krum aggregation (Blanchard et al., 2017, originally developed for Byzantine-robust federated learning): select the score with the smallest sum of squared distances to its nearest neighbors. Breakdown point: approximately 29% for k=5 reviewers.

The Corruption Risk Score detects suspicious reviewers using three components: deviation from the global mean, pairwise correlation with other reviewers (collusion signal), and variance collapse. The key empirical signature of a colluding reviewer panel is counter-intuitive. Collusion reduces score variance, not increases it, because reviewers pre-coordinate to look legitimate.

The original fixed threshold for this score produced a 47% false positive rate, nearly every clean reviewer getting flagged. The fix is a permutation-based null distribution: randomly permute each reviewer's score assignments 80 times, compute their CRS under the null hypothesis of no corruption, and threshold the observed CRS against the 95th percentile of that distribution. False positive rate drops to approximately 5%.

**Layer 5: Randomised Softmax Selection.** The selection probability follows a softmax over estimated quality, with temperature parameter alpha drawn uniformly from [3, 8] each cycle. Crucially, alpha is not revealed until after selection. If applicants know the exact selection function, they can optimize against it. Randomizing alpha destroys the optimization target.

---

## The Stress Test: 15 Attack Vectors

The most important part of this project was adversarial testing. I defined 15 attack scenarios, seven based on documented empirical patterns from EU, US, and Canadian procurement data, and eight forward-looking scenarios based on emerging threats. Each ran 80-150 Monte Carlo simulations.

The seven empirical scenarios:

| Scenario | Result | Key Metric |
|---|---|---|
| Sparse bribery (1 corrupt reviewer) | Resistant | CPR = 0.00 |
| Reviewer collusion ring (3 reviewers) | Resistant | CPR = 0.00 |
| Specification gaming | Critical failure | CPR = 0.47 |
| Cartel bid rotation | Partial | CPR = 0.11 |
| Identity splitting (shell companies) | Resistant | CPR = 0.09 |
| Administrative capture | Resistant | CPR = 0.00 |
| False input data | Resistant | CPR = 0.00 |

CPR is Corruption Penetration Rate, the fraction of selected proposals that are corrupt beneficiaries.

The most important result is the critical failure on specification gaming. After three full versions of the algorithm and targeted fixes, CPR remained at 0.47 with a corruption ROI of 4.19. For every unit invested in gaming the rubric, a corrupt applicant gets 4.19 units back. No mechanism I implemented meaningfully dented this.

Why? Because specification gaming is legal. The applicant never interacts with a reviewer corruptly. They simply optimize their proposal for the scoring rubric, which is exactly what the system tells them to do. There is no signal for a detection system to find. The two-layer rubric mitigates it (dropping ROI from ~6 to ~4), but does not resist it. The honest conclusion is that this requires a qualitative human evaluation component that resists formalization, a process reform, not a technical fix.

---

## Eight Future Scenarios: Where AI Changes Everything

The more interesting stress tests were forward-looking. Here are three that are directly relevant to an AI audience.

**C8: LLM-Assisted Perfect Gaming.** A procurement consulting firm fine-tunes a language model on 10,000 past winning proposals and uses it to generate applications that score at ceiling on every public rubric dimension simultaneously. The detection fingerprint: genuine proposals have natural trade-offs across scoring dimensions (strong on innovation, weaker on cost-effectiveness). LLM-generated proposals show near-uniform high scores across all dimensions, variance across rubric dimensions below 0.15 on a normalized scale.

The system detects this through a semantic diversity fingerprint, flagging proposals in the top quartile of scores with anomalously low cross-dimension variance. This works, but imperfectly. Sophisticated adversaries can inject artificial variance while maintaining high scores, and the arms race between the generator and the detector is not obviously winnable.

**C9: AI Reviewer Homogeneity.** Not all AI effects on procurement are intentional corruption. Sixty percent of reviewers using the same institutional AI tool to structure their reviews produces score correlations approaching 0.90, triggering the variance collapse detection mechanism as a false positive. The system cannot distinguish "reviewers colluded" from "reviewers used the same AI assistant and reached similar conclusions." This is a genuine unsolved problem. The empirical null distribution approach helps, but it requires continuous recalibration as AI tool adoption changes.

**C12: State Actor Infiltration.** A foreign state systematically places agents in academic expert reviewer pools, targeting dual-use technology procurement. Agents have genuine credentials and high expertise scores. They are not fake reviewers. They subtly redirect awards toward firms with supply-chain connections to state-adjacent entities. The detection challenge: their individual CRS scores are normal. The signal appears only in the aggregate outcome, which categories are being redirected and which firms are winning. This requires cross-cycle pattern analysis that the single-round simulation understates.

---

## The Cross-Algorithm Connection

The most intellectually satisfying part of this project was discovering how many existing algorithmic frameworks map directly onto the procurement corruption problem.

**Byzantine Fault Tolerance** (Lamport et al., 1982) formalizes the reviewer corruption problem precisely: a corrupt reviewer is a Byzantine node, one that behaves arbitrarily rather than following protocol. PBFT guarantees safety with f Byzantine nodes if total nodes n >= 3f + 1. For k=3 reviewers per proposal, you tolerate exactly 0 Byzantine failures. For k=7, you tolerate 2. This is why the minimum reviewer count matters more than almost any other parameter.

**Bayesian Truth Serum** (Prelec, 2004) offers a theoretically clean solution to reviewer honesty: reward reviewers whose reports are surprisingly predictive of other reviewers' reports, even without ground truth. A reviewer who inflates scores for bribes will find their report is not surprisingly accurate. Honest reviewers don't report the same inflation. BTS makes honest reporting a Nash equilibrium. The practical limitation is that it fails when reviewers share a strong common prior, which is empirically common in specialized fields with small reviewer communities.

**Krum aggregation** (Blanchard et al., 2017), developed for Byzantine-robust federated learning, turns out to be directly applicable to multi-reviewer score aggregation. The mathematical structure is identical: aggregate a set of vectors where some are adversarially corrupted, maintain a bounded influence function, and achieve a useful breakdown point. The transfer from FL to procurement is essentially direct.

**EigenTrust** (Kamvar et al., 2003), a peer-to-peer reputation algorithm, provides the right framework for reviewer reputation over time. Trust propagates transitively through the reviewer-reviewer endorsement graph, weighted by prediction accuracy. A reviewer whose highly-scored proposals consistently underdeliver loses trust; so do the reviewers who trusted that reviewer. The PageRank analogy is precise.

These are not superficial analogies. The mathematical structure of each algorithm applies without modification.

---

## What the Economic Model Says

I built a formal cost-benefit model calibrated to actual procurement volumes and empirically-derived effectiveness estimates. The numbers under moderate assumptions:

**United States:** $700B annual federal procurement, 22% estimated waste ($154B). An 18% waste reduction generates $27.7B in annual savings. Ten-year deployment and operating costs: $377M. Ten-year net savings: $262B. Breakeven: Year 1.

**Canada:** CAD$37B annual procurement, 20% waste. Moderate recovery: CAD$1.08B/year. Ten-year cost: CAD$86M. Net return: CAD$10.1B. Breakeven: Year 1.

---

## What I Couldn't Fix, and Why That Matters

The honest part of any technical project is the failure register. Three things resisted every mechanism I tried.

**Specification gaming** (CPR 47%) requires qualitative human oversight and institutional reform, not a better algorithm. Any sufficiently specified rubric becomes gameable.

**Democratic capture**, reviewers systematically favoring politically connected applicants out of career self-interest, is statistically detectable only years after the damage is done, through post-award performance analysis. No real-time mechanism touches it. It requires independent oversight bodies with political insulation.

**Complete institutional capture.** If the ministry or oversight function is itself corrupt, no procurement algorithm survives. This is a constitutional problem, not a technical one.

Documenting these constraints is as important as documenting what the system does. A system that claims to solve everything is a system that can't be trusted.

---

## What Comes Next

The simulation code, all six algorithm versions, the US and Canada jurisdiction modules, and the full documentation package are on GitHub. The system integrates with SAM.gov and FPDS-NG (US) and Buyandsell.gc.ca/MERX (Canada), and is legally aligned with FAR, GCR, CFTA, and PSPC Supply Manual requirements.

The most important next step is empirical validation on real procurement data. The synthetic calibration against EU Structural Funds statistics is strong, but the ML classifier and empirical CRS calibrator need real FPDS-NG or PSPC clean-subset data to reach full effectiveness. That requires an institutional partner.

If you work in or study public administration, procurement integrity, or applied mechanism design and find this interesting, I'd genuinely like to hear from you.

---

*This project grew out of a research interest in mechanism design applied to real-world institutions. The simulation framework runs approximately 80-150 Monte Carlo iterations per scenario across 15 attack vectors, with empirical calibration against EU Structural Funds (Fazekas & Kocsis, 2020), US FPDS-NG, and Canadian PSPC audit data. Full technical specification, jurisdiction implementation guides, and source code available on GitHub.*

*The author is a second-year Computer Science and Anthropology student at McGill University.*
