#!/usr/bin/env python3
"""
GrantGuard V7 - Adversarial Audit
Three diagnostics, in order:
  #3  Detection-Resistance Decoupling Audit  (which flags actually change outcomes)
  #1  Adaptive Best-Response Adversary        (attacker optimises against the detector)
  #2  Centroid ("a little is enough") attack on Krum  (Baruch et al. 2019)

Run from the simulation/ directory:  python grantguard_v7.py
"""

import numpy as np
import warnings, sys, time
from collections import defaultdict

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

from grantguard_simulation import DataGenerator, ScoringEngine
from grantguard_simulation import SCENARIOS as SCENARIOS_V3
from grantguard_v5 import (
    cfg5, DataGeneratorV5, ScoringEngineV5, DetectionEngineV5,
    AllocationSystemV5, AIGamingDetector, krum_aggregate,
    FUTURE_SCENARIOS, ALL_SCENARIOS_V5,
)

SEED = 42

# The audit only needs RELATIVE comparisons, so trade permutation precision for
# speed. 40 permutations is plenty to rank modes/attacks; the shipped default is 400.
cfg5.CRS_PERMUTATIONS = 40

# Comprehensive corrupt-type set. NOTE: V3's compute_metrics only counts
# {'gaming','shell','shell_parent','colluder'} -- so it scores CPR=0 for
# llm_gamed / synthetic / political attacks. The audit computes its own CPR
# with the full set so the numbers are honest. (This discrepancy is finding 0.)
CORRUPT_TYPES = {
    'gaming', 'llm_gamed', 'llm_adaptive', 'shell', 'shell_parent',
    'colluder', 'synthetic', 'arbitrage_sub', 'arbitrage_parent',
    'politically_connected',
}


def true_cpr(applicants, selected_ids):
    amap = {a.id: a for a in applicants}
    if not selected_ids:
        return 0.0
    corrupt = sum(1 for sid in selected_ids
                  if amap.get(sid) and amap[sid].applicant_type in CORRUPT_TYPES)
    return corrupt / len(selected_ids)


# ════════════════════════════════════════════════════════════════════════════
#  #1  ADAPTIVE ADVERSARY  +  #2 CENTROID ATTACK : generator / scoring / detector
# ════════════════════════════════════════════════════════════════════════════

class DataGeneratorV7(DataGeneratorV5):
    """Adds adaptive-LLM gamers (C8b) and centroid-collusion targets (C2b)."""
    def generate_applicants(self, n, scenario):
        applicants = super().generate_applicants(n, scenario)
        stype = scenario.get('type', '')
        if stype == 'C8b_llm_adaptive':
            boost = scenario.get('llm_boost', 2.8)
            frac  = scenario.get('llm_frac', 0.35)
            for a in applicants:
                if self.rng.random() < frac:
                    a.gaming_boost = boost
                    a.observable_features = min(10.0, a.observable_features + boost)
                    a.applicant_type = 'llm_adaptive'
        return applicants

    def generate_reviewers(self, m, scenario):
        reviewers = super().generate_reviewers(m, scenario)
        if scenario.get('type', '') == 'C2b_centroid_collusion':
            rs = scenario.get('ring_size', 3)
            for idx in self.rng.choice(m, min(rs, m), replace=False):
                reviewers[idx].is_corrupt = True
        return reviewers


class ScoringEngineV7(ScoringEngineV5):
    """Adds the centroid (Baruch 'a little is enough') collusion attack."""
    def generate_scores(self, applicants, reviewers, assignments, scenario):
        raw_scores, mean_infl = super().generate_scores(
            applicants, reviewers, assignments, scenario)
        if scenario.get('type', '') == 'C2b_centroid_collusion':
            delta = scenario.get('centroid_delta', 0.8)   # small, stays in inlier ball
            corrupt_ids = {r.id for r in reviewers if r.is_corrupt}
            ranked = sorted(applicants, key=lambda a: a.observable_features, reverse=True)
            targets = {a.id for a in ranked[:scenario.get('n_targets', 2)]}
            for app_id in targets:
                rs = raw_scores.get(app_id, {})
                honest = [s for rid, s in rs.items() if rid not in corrupt_ids]
                if not honest:
                    continue
                centroid = float(np.mean(honest))
                # colluders cluster tightly just *above* the honest centroid:
                # not outliers, so Krum can't reject them, but they form the
                # densest cluster -> Krum selects from them, dragging the pick up.
                for rid in rs:
                    if rid in corrupt_ids:
                        rs[rid] = float(np.clip(
                            centroid + delta + self.rng.normal(0, 0.03), 0, 10))
        return raw_scores, mean_infl


class AIGamingDetectorV7(AIGamingDetector):
    """Models the adaptive adversary: 'llm_adaptive' injects calibrated variance
    to sit just *above* the uniformity threshold, evading the std<0.15 filter."""
    def score_dimensions(self, applicant, gaming_type='none'):
        if gaming_type == 'adaptive':
            base = applicant.true_quality / 10.0
            target_mean = min(1.0, base + applicant.gaming_boost / 10.0)
            # std deliberately just above thresh (0.15) -> looks "natural"
            evade_std = self.thresh + 0.05
            return np.clip(self.rng.normal(target_mean, evade_std, self.n_dims), 0, 1)
        return super().score_dimensions(applicant, gaming_type)

    def detect_llm_gaming(self, applicants, aggregated_scores):
        if not aggregated_scores:
            return [], {}
        top_q = np.percentile(list(aggregated_scores.values()), 75)
        flagged, uniformity = [], {}
        for a in applicants:
            gt = ('llm' if a.applicant_type == 'llm_gamed'
                  else 'adaptive' if a.applicant_type == 'llm_adaptive'
                  else 'human' if a.applicant_type == 'gaming'
                  else 'none')
            dims = self.score_dimensions(a, gt)
            u = float(np.std(dims))
            uniformity[a.id] = u
            if u < self.thresh and aggregated_scores.get(a.id, 0.0) >= top_q * 0.75:
                flagged.append(a.id)
        return flagged, uniformity


class DetectionEngineV7(DetectionEngineV5):
    def __init__(self, rng):
        super().__init__(rng)
        self.ai_detector = AIGamingDetectorV7(rng)


# ════════════════════════════════════════════════════════════════════════════
#  AUDIT RUNNER : a single pipeline with toggles for coupling ablations (#3)
# ════════════════════════════════════════════════════════════════════════════

class AuditRunner:
    """
    coupling modes:
      'baseline'        - V5 as shipped (Krum + CRS down-weighting; flags observational)
      'no_crs'          - CRS forced to 0 in aggregation (isolates CRS's real effect)
      'drop_high_crs'   - high-CRS reviewers fully removed before aggregation (coupled)
      'drop_flagged_app'- applicants in variance-collapse set made ineligible (coupled)
    """
    def __init__(self, coupling='baseline'):
        self.coupling = coupling
        self.master = np.random.default_rng(cfg5.RANDOM_SEED)

    def single_run(self, scenario):
        rng = np.random.default_rng(int(self.master.integers(0, 2**31)))
        gen = DataGeneratorV7(rng)
        scorer = ScoringEngineV7(rng)
        detector = DetectionEngineV7(rng)
        allocator = AllocationSystemV5(rng)

        applicants = gen.generate_applicants(cfg5.N_APPLICANTS, scenario)
        reviewers  = gen.generate_reviewers(cfg5.N_REVIEWERS, scenario)
        network    = gen.generate_network(applicants, reviewers)
        assignments = scorer.assign_reviewers(applicants, reviewers, network)
        scores, _ = scorer.generate_scores(applicants, reviewers, assignments, scenario)

        pre_agg = allocator.aggregate_scores(
            applicants, scores, {r.id: 0.0 for r in reviewers}, [])
        flags, fp, fn, crs, high_crs = detector.run_full_detection(
            applicants, reviewers, scores, assignments, network,
            scenario, aggregated_scores=pre_agg)

        # variance-collapse suspects (recomputed for the coupling ablation)
        k = len(next(iter(scores.values()))) if scores else cfg5.K_PER_PROPOSAL
        vthr = cfg5.BASE_VARIANCE_THRESH * np.sqrt(max(k, 1) / cfg5.K_PER_PROPOSAL)
        susp = {aid for aid, rs in scores.items()
                if len(rs) >= 2 and np.std(list(rs.values())) < vthr}

        # ---- apply coupling mode ----
        eff_scores = scores
        eff_crs = crs
        if self.coupling == 'no_crs':
            eff_crs = {r.id: 0.0 for r in reviewers}
        elif self.coupling == 'drop_high_crs':
            hc = set(high_crs)
            eff_scores = {aid: {rid: s for rid, s in rs.items() if rid not in hc}
                          for aid, rs in scores.items()}
            eff_scores = {aid: rs for aid, rs in eff_scores.items() if rs}

        aggregated = allocator.aggregate_scores(applicants, eff_scores, eff_crs, high_crs)

        eligible = applicants
        if self.coupling == 'drop_flagged_app':
            eligible = [a for a in applicants if a.id not in susp] or applicants

        selected = allocator.select_proposals(eligible, aggregated, scenario, flags)

        return {
            'flags': flags,
            'cpr': true_cpr(applicants, selected),
            'selected': selected,
            'susp_corrupt_flagged': len(
                {a.id for a in applicants if a.applicant_type in CORRUPT_TYPES} & susp),
            'n_corrupt': sum(1 for a in applicants if a.applicant_type in CORRUPT_TYPES),
            'llm_detected': 'ai_gaming_detected' in flags,
        }

    def run(self, scenario, n=60):
        return [self.single_run(scenario) for _ in range(n)]


def mean(xs):
    return float(np.mean(xs)) if xs else 0.0


# ════════════════════════════════════════════════════════════════════════════
#  #3  DETECTION-RESISTANCE DECOUPLING AUDIT
# ════════════════════════════════════════════════════════════════════════════

def audit_3_decoupling(n=60):
    print("=" * 74)
    print("#3  DETECTION-RESISTANCE DECOUPLING AUDIT")
    print("=" * 74)
    corrupt_scenarios = {k: v for k, v in ALL_SCENARIOS_V5.items() if v.get('type')}

    # (a) flag -> CPR table under baseline
    base = AuditRunner('baseline')
    flag_cpr = defaultdict(list)
    noflag_cpr = defaultdict(list)
    all_flags = set()
    per_scenario_cpr = {}
    for name, sc in corrupt_scenarios.items():
        runs = base.run(sc, n)
        per_scenario_cpr[name] = mean([r['cpr'] for r in runs])
        for r in runs:
            fset = set(r['flags'])
            all_flags |= fset
            for f in fset:
                flag_cpr[f].append(r['cpr'])
        # for "no-flag" baseline per flag, collect runs where flag absent
        for f in all_flags:
            for r in runs:
                if f not in r['flags']:
                    noflag_cpr[f].append(r['cpr'])

    print("\n(a) Mean CPR when a flag fires vs when it does not")
    print(f"    {'flag':<28}{'CPR|fired':>10}{'CPR|absent':>12}{'protective?':>13}")
    print("    " + "-" * 63)
    for f in sorted(all_flags):
        cf, ca = mean(flag_cpr[f]), mean(noflag_cpr[f])
        verdict = 'yes' if cf < ca - 0.03 else 'DECORATIVE'
        print(f"    {f:<28}{cf:>10.3f}{ca:>12.3f}{verdict:>13}")

    # (b) structural ablation: does anything except CRS change the outcome?
    print("\n(b) Coupling ablation - mean CPR across all corrupt scenarios")
    modes = ['baseline', 'no_crs', 'drop_high_crs', 'drop_flagged_app']
    abl = {}
    for m in modes:
        r = AuditRunner(m)
        cprs = [mean([x['cpr'] for x in r.run(sc, n)])
                for sc in corrupt_scenarios.values()]
        abl[m] = mean(cprs)
    print(f"    {'mode':<20}{'mean CPR':>10}{'delta vs baseline':>20}")
    print("    " + "-" * 50)
    for m in modes:
        print(f"    {m:<20}{abl[m]:>10.3f}{abl[m]-abl['baseline']:>20.3f}")

    print("\n  READING:")
    print("  - 'baseline' vs 'no_crs' = the ONLY coupling currently wired in.")
    print("  - 'drop_high_crs' / 'drop_flagged_app' = CPR if flags were ACTUALLY")
    print("    coupled to selection (value currently left on the table).")
    return abl, per_scenario_cpr


# ════════════════════════════════════════════════════════════════════════════
#  #1  ADAPTIVE ADVERSARY
# ════════════════════════════════════════════════════════════════════════════

def _build(rng, scenario):
    """Construct one full realization and return the live objects."""
    gen = DataGeneratorV7(rng); scorer = ScoringEngineV7(rng)
    det = DetectionEngineV7(rng); alloc = AllocationSystemV5(rng)
    apps = gen.generate_applicants(cfg5.N_APPLICANTS, scenario)
    revs = gen.generate_reviewers(cfg5.N_REVIEWERS, scenario)
    net  = gen.generate_network(apps, revs)
    assign = scorer.assign_reviewers(apps, revs, net)
    scores, _ = scorer.generate_scores(apps, revs, assign, scenario)
    return apps, revs, net, assign, scores, det, alloc


def audit_1_adaptive(n=80):
    print("\n" + "=" * 74)
    print("#1  ADAPTIVE BEST-RESPONSE ADVERSARY (vs uniformity detector)")
    print("=" * 74)
    static = {'type': 'C8_llm_gaming',  'llm_boost': cfg5.LLM_GAMING_BOOST, 'llm_frac': 0.35}
    adapt  = {'type': 'C8b_llm_adaptive','llm_boost': cfg5.LLM_GAMING_BOOST, 'llm_frac': 0.35}
    master = np.random.default_rng(SEED + 1)

    print(f"\n  {'attacker':<24}{'gamer recall':>14}{'gamer win rate':>16}{'CPR':>8}")
    print("  " + "-" * 62)
    for label, sc in [('C8  static gamer', static), ('C8b adaptive gamer', adapt)]:
        recalls, wins, cprs = [], [], []
        gtypes = {'llm_gamed', 'llm_adaptive'}
        for _ in range(n):
            rng = np.random.default_rng(int(master.integers(0, 2**31)))
            apps, revs, net, assign, scores, det, alloc = _build(rng, sc)
            agg = alloc.aggregate_scores(apps, scores, {r.id: 0.0 for r in revs}, [])
            gamers = {a.id for a in apps if a.applicant_type in gtypes}
            flagged, _ = det.ai_detector.detect_llm_gaming(apps, agg)
            flagged = set(flagged)
            sel = set(alloc.select_proposals(apps, agg, sc, []))
            if gamers:
                recalls.append(len(flagged & gamers) / len(gamers))   # caught as gamer
                wins.append(len(sel & gamers) / len(gamers))          # got funded
            cprs.append(true_cpr(apps, sel))
        print(f"  {label:<24}{mean(recalls):>13.1%}{mean(wins):>16.1%}{mean(cprs):>8.3f}")
    print("\n  READING: 'gamer recall' = share of REAL gamers the uniformity filter")
    print("  actually catches. If recall collapses static->adaptive while win rate")
    print("  holds, the adaptive adversary has evaded detection - the arms race.")


# ════════════════════════════════════════════════════════════════════════════
#  #2  CENTROID ATTACK ON KRUM
# ════════════════════════════════════════════════════════════════════════════

def audit_2_centroid(n=80):
    print("\n" + "=" * 74)
    print("#2  CENTROID ATTACK ON KRUM  (Baruch et al. 2019, 'a little is enough')")
    print("=" * 74)
    naive = {'type': 'C2_collusion', 'ring_size': 3,
             'coordination_quality': 0.80, 'bribe_amount': 1.8, 'n_targets': 2}
    centroid = {'type': 'C2b_centroid_collusion', 'ring_size': 3,
                'centroid_delta': 0.8, 'n_targets': 2}
    master = np.random.default_rng(SEED + 2)

    # Correct metric for reviewer-collusion: how much does the manipulation move
    # the Krum-aggregated score of the target ABOVE the honest reviewers' mean,
    # and does it stay under the CRS radar?
    print(f"\n  {'attacker':<26}{'Krum inflation':>16}{'high_crs rate':>15}{'target won':>13}")
    print("  " + "-" * 70)
    for label, sc in [('C2  naive offset', naive),
                      ('C2b centroid (inlier)', centroid)]:
        infl, flags_hc, won = [], [], []
        for _ in range(n):
            rng = np.random.default_rng(int(master.integers(0, 2**31)))
            apps, revs, net, assign, scores, det, alloc = _build(rng, sc)
            corrupt = {r.id for r in revs if r.is_corrupt}
            ranked = sorted(apps, key=lambda a: a.observable_features, reverse=True)
            targets = [a.id for a in ranked[:sc.get('n_targets', 2)]]
            for tid in targets:
                rs = scores.get(tid, {})
                honest = [s for rid, s in rs.items() if rid not in corrupt]
                if not honest or len(rs) < 3:
                    continue
                krum_pick = krum_aggregate({tid: rs}, k=cfg5.K_PER_PROPOSAL, f=cfg5.KRUM_F)[tid]
                infl.append(krum_pick - float(np.mean(honest)))   # >0 = manipulation moved Krum up
            flags, *_ , high_crs = det.run_full_detection(
                apps, revs, scores, assign, net, sc,
                aggregated_scores=alloc.aggregate_scores(
                    apps, scores, {r.id: 0.0 for r in revs}, []))
            flags_hc.append(1.0 if 'high_crs_reviewer' in flags else 0.0)
            sel = set(alloc.select_proposals(apps, alloc.aggregate_scores(
                apps, scores, {r.id: 0.0 for r in revs}, []), sc, flags))
            won.append(1.0 if set(targets) & sel else 0.0)
        print(f"  {label:<26}{mean(infl):>16.3f}{mean(flags_hc):>15.1%}{mean(won):>13.1%}")
    print("\n  READING: 'Krum inflation' = how far the colluders dragged the Krum")
    print("  pick above the honest mean. Naive offset -> colluders are outliers")
    print("  Krum trims (inflation ~0 or negative) AND they trip high_crs. The")
    print("  centroid attack should show POSITIVE inflation with LOWER high_crs =")
    print("  it moved the result while staying invisible.")


# ════════════════════════════════════════════════════════════════════════════
#  #2 (clean)  CENTROID ATTACK ON KRUM - confound-free harness
#    - full ring CO-ASSIGNED to one proposal (real f-of-k Byzantine test)
#    - target is a MEDIOCRE applicant (rank ~65th pct) that should NOT win,
#      so "target funded" = genuine corruption, not pre-existing quality
#    - attack-OFF counterfactual isolates the pure attack effect
#    - sweep over ring size x delta
# ════════════════════════════════════════════════════════════════════════════

def audit_2b_clean(n=60):
    from grantguard_simulation import DataGenerator as DG
    print("\n" + "=" * 78)
    print("#2 (CLEAN)  CENTROID ATTACK ON KRUM - co-assigned ring, mediocre target")
    print("=" * 78)
    K = cfg5.K_PER_PROPOSAL
    N = cfg5.N_APPLICANTS
    M = cfg5.N_REVIEWERS
    budget = max(1, int(N * cfg5.BUDGET_FRACTION))
    master = np.random.default_rng(SEED + 7)
    alloc = AllocationSystemV5(np.random.default_rng(0))

    print(f"\n  k={K} reviewers/proposal, budget funds top {budget}/{N}, "
          f"Krum f={cfg5.KRUM_F}")
    print(f"\n  {'ring/k':>7}{'delta':>7}{'Krum infl':>11}{'won|attack':>12}"
          f"{'won|OFF':>10}{'pure effect':>13}{'high_crs':>10}")
    print("  " + "-" * 70)

    for ring_size in (2, 3):
        for delta in (0.5, 1.0, 2.0):
            infl, won_on, won_off, hc = [], [], [], []
            for _ in range(n):
                rng = np.random.default_rng(int(master.integers(0, 2**31)))
                gen = DG(rng)
                apps = gen.generate_applicants(N, {})
                revs = gen.generate_reviewers(M, {})
                ring = set(r.id for r in revs[:ring_size])   # designate the ring

                # mediocre target: rank ~65th percentile by true quality (won't win)
                ranked = sorted(apps, key=lambda a: a.true_quality, reverse=True)
                target = ranked[int(N * 0.65)]

                # ---- assignment: ring co-assigned to target; others random ----
                assign = {}
                non_ring = [r.id for r in revs if r.id not in ring]
                for a in apps:
                    if a.id == target.id:
                        extra = rng.choice(non_ring, K - ring_size, replace=False).tolist()
                        assign[a.id] = list(ring) + extra
                    else:
                        assign[a.id] = rng.choice(M, K, replace=False).tolist()

                # ---- honest scores everywhere ----
                rmap = {r.id: r for r in revs}
                honest_scores = {}
                for a in apps:
                    honest_scores[a.id] = {}
                    for rid in assign[a.id]:
                        r = rmap[rid]
                        honest_scores[a.id][rid] = float(np.clip(
                            a.true_quality + r.bias + rng.normal(0, r.noise_level), 0, 10))

                # centroid of the honest (non-ring) reviewers on the target
                t_honest = [s for rid, s in honest_scores[target.id].items() if rid not in ring]
                centroid = float(np.mean(t_honest)) if t_honest else 5.0

                # ---- attack ON: ring clusters tightly at centroid+delta ----
                atk = {aid: dict(rs) for aid, rs in honest_scores.items()}
                for rid in ring:
                    if rid in atk[target.id]:
                        atk[target.id][rid] = float(np.clip(
                            centroid + delta + rng.normal(0, 0.03), 0, 10))

                # Krum inflation on target
                kp = krum_aggregate({target.id: atk[target.id]}, k=K, f=cfg5.KRUM_F)[target.id]
                infl.append(kp - centroid)

                # selection: attack ON vs OFF (honest counterfactual)
                agg_on  = alloc.aggregate_scores(apps, atk, {r.id: 0.0 for r in revs}, [])
                agg_off = alloc.aggregate_scores(apps, honest_scores, {r.id: 0.0 for r in revs}, [])
                # deterministic top-budget selection (remove softmax randomness)
                top_on  = {a.id for a in sorted(apps, key=lambda a: agg_on[a.id],  reverse=True)[:budget]}
                top_off = {a.id for a in sorted(apps, key=lambda a: agg_off[a.id], reverse=True)[:budget]}
                won_on.append(1.0 if target.id in top_on else 0.0)
                won_off.append(1.0 if target.id in top_off else 0.0)

                # does CRS catch any ring member? (single-target collusion)
                det = DetectionEngineV7(rng)
                _, _, high_crs = det.perm_crs.compute_crs_with_thresholds(revs, atk, rng)
                hc.append(1.0 if (ring & set(high_crs)) else 0.0)

            pure = mean(won_on) - mean(won_off)
            print(f"  {f'{ring_size}/{K}':>7}{delta:>7.1f}{mean(infl):>11.3f}"
                  f"{mean(won_on):>12.1%}{mean(won_off):>10.1%}{pure:>13.1%}{mean(hc):>10.1%}")

    print("\n  READING: a MEDIOCRE target (65th pct) should never win (won|OFF ~ 0).")
    print("  'pure effect' = how often the ring lifts it into the funded set that")
    print("  it would NOT otherwise reach. 'high_crs' = how often CRS catches the")
    print("  ring. High pure effect + low high_crs = Krum+CRS genuinely bypassed.")


if __name__ == '__main__':
    t0 = time.time()
    audit_3_decoupling(n=40)
    audit_1_adaptive(n=60)
    audit_2_centroid(n=60)
    audit_2b_clean(n=60)
    print(f"\nAll audits complete in {time.time()-t0:.1f}s")
