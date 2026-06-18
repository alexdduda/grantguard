#!/usr/bin/env python3
"""
GrantGuard V8 - Hardening pass, validated against the V7 audits.

Defenses, each re-tested against the attack that motivated it:
  D1  Anti-collusion aggregation     -> beats the centroid Krum bypass (#2)
        - compare Krum vs Median vs CARA (cluster-aware robust aggregation)
        - x k=5/7  x ring=2/3  x dispersion off/on
  D2  Confidential rubric            -> beats the adaptive LLM gamer (#1)
        - sweep confidential weight; gaming boost only touches VISIBLE dims
  D3  Validated coupling + FP harm   -> only couple a signal that actually works,
        and measure the wrongful-exclusion cost on clean data

Run from simulation/:  python grantguard_v8.py     (fast - no permutation CRS)
"""

import numpy as np
import warnings, sys, time
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

from grantguard_simulation import DataGenerator, Config

cfg = Config()
SEED = 42
N, M = cfg.N_APPLICANTS, cfg.N_REVIEWERS
BUDGET = max(1, int(N * cfg.BUDGET_FRACTION))


def mean(xs):
    return float(np.mean(xs)) if len(xs) else 0.0


# ── scalar aggregators ───────────────────────────────────────────────────────
def krum_scalar(vals, f=1):
    v = list(vals); n = len(v)
    if n <= 2:
        return float(np.median(v))
    m = max(1, n - f - 2)
    best, bs = None, np.inf
    for i, vi in enumerate(v):
        d = sorted((vi - vj) ** 2 for j, vj in enumerate(v) if j != i)
        t = sum(d[:m])
        if t < bs:
            bs, best = t, vi
    return float(best)


def median_scalar(vals, **_):
    return float(np.median(list(vals)))


def cara_scalar(vals, margin=1.2, **_):
    """Cluster-Aware Robust Aggregation: split at the largest gap; if a minority
    cluster sits ABOVE the rest by > margin, trust the larger (honest) group.
    Krum trims lone outliers; CARA trims a coordinated *subcluster*."""
    v = sorted(vals); n = len(v)
    if n <= 2:
        return float(np.median(v))
    gaps = [(v[i + 1] - v[i], i) for i in range(n - 1)]
    g, idx = max(gaps)
    if g > margin:
        lower, upper = v[:idx + 1], v[idx + 1:]
        grp = lower if len(lower) >= len(upper) else upper
        return float(np.mean(grp))
    t = max(0, n // 6)
    vv = v[t:n - t] if t else v
    return float(np.mean(vv))


AGGS = {'krum': krum_scalar, 'median': median_scalar, 'cara': cara_scalar}


# ── shared realization builder (clean, no scenario plumbing) ─────────────────
def realize(rng, ring_size, delta, k, dispersion):
    """Return (apps, true_q, target_id, scores_attack, scores_honest)."""
    gen = DataGenerator(rng)
    apps = gen.generate_applicants(N, {})
    revs = gen.generate_reviewers(M, {})
    rmap = {r.id: r for r in revs}
    ring = [r.id for r in revs[:ring_size]]
    non_ring = [r.id for r in revs if r.id not in ring]

    ranked = sorted(apps, key=lambda a: a.true_quality, reverse=True)
    target = ranked[int(N * 0.65)]            # mediocre: should not win

    # assignment
    assign = {}
    n_ring_on_target = 1 if dispersion else ring_size   # dispersion caps ring/panel
    for a in apps:
        if a.id == target.id:
            chosen_ring = ring[:n_ring_on_target]
            extra = rng.choice(non_ring, k - len(chosen_ring), replace=False).tolist()
            assign[a.id] = chosen_ring + extra
        else:
            assign[a.id] = rng.choice(M, k, replace=False).tolist()

    honest = {}
    for a in apps:
        honest[a.id] = {}
        for rid in assign[a.id]:
            r = rmap[rid]
            honest[a.id][rid] = float(np.clip(
                a.true_quality + r.bias + rng.normal(0, r.noise_level), 0, 10))

    ring_on_t = set(assign[target.id]) & set(ring)
    t_honest = [s for rid, s in honest[target.id].items() if rid not in ring_on_t]
    centroid = float(np.mean(t_honest)) if t_honest else 5.0

    attack = {aid: dict(rs) for aid, rs in honest.items()}
    for rid in ring_on_t:
        attack[target.id][rid] = float(np.clip(
            centroid + delta + rng.normal(0, 0.03), 0, 10))

    true_q = {a.id: a.true_quality for a in apps}
    return apps, true_q, target.id, attack, honest


def funded(scores, agg_fn, k):
    agg = {aid: agg_fn(list(rs.values())) for aid, rs in scores.items()}
    return {aid for aid in sorted(agg, key=lambda x: agg[x], reverse=True)[:BUDGET]}


# ════════════════════════════════════════════════════════════════════════════
#  D1  ANTI-COLLUSION AGGREGATION  (re-test of #2)
# ════════════════════════════════════════════════════════════════════════════
def d1_anticollusion(n=120):
    print("=" * 80)
    print("D1  ANTI-COLLUSION AGGREGATION  (re-test of the #2 centroid bypass)")
    print("=" * 80)
    print(f"\n  Mediocre target, delta=2.0. 'pure effect' = how often the ring funds it")
    print(f"  beyond the no-attack baseline. Lower = better defense.\n")
    print(f"  {'k':>3}{'ring':>5}{'dispersion':>12}"
          f"{'krum':>9}{'median':>9}{'cara':>9}")
    print("  " + "-" * 56)
    master = np.random.default_rng(SEED)
    delta = 2.0
    for k in (5, 7):
        for ring_size in (2, 3):
            for dispersion in (False, True):
                pe = {name: [] for name in AGGS}
                for _ in range(n):
                    rng = np.random.default_rng(int(master.integers(0, 2**31)))
                    apps, tq, tid, atk, hon = realize(rng, ring_size, delta, k, dispersion)
                    for name, fn in AGGS.items():
                        won_on = tid in funded(atk, fn, k)
                        won_off = tid in funded(hon, fn, k)
                        pe[name].append(1.0 if (won_on and not won_off) else 0.0)
                disp = 'on' if dispersion else 'off'
                print(f"  {k:>3}{ring_size:>5}{disp:>12}"
                      f"{mean(pe['krum']):>9.1%}{mean(pe['median']):>9.1%}{mean(pe['cara']):>9.1%}")
    print("\n  READING: krum is the shipped defense. median already helps when the")
    print("  ring is a MINORITY of k. cara adds cluster-trim. dispersion='on'")
    print("  (COI graph keeps the ring to <=1 per panel) is the structural fix for")
    print("  a MAJORITY ring - which no aggregator alone can survive.")


# ════════════════════════════════════════════════════════════════════════════
#  D2  CONFIDENTIAL RUBRIC  (re-test of #1)
# ════════════════════════════════════════════════════════════════════════════
def d2_confidential(n=200):
    print("\n" + "=" * 80)
    print("D2  CONFIDENTIAL RUBRIC  (re-test of the #1 adaptive LLM gamer)")
    print("=" * 80)
    print(f"\n  Gaming boost only touches the VISIBLE rubric. As confidential weight")
    print(f"  rises, the un-gameable portion dominates and the gamer's edge fades.\n")
    print(f"  {'conf weight':>12}{'gamer win rate':>16}{'honest win rate':>17}"
          f"{'quality eff':>13}")
    print("  " + "-" * 58)
    master = np.random.default_rng(SEED + 3)
    boost = 2.8
    frac = 0.35
    for cw in (0.0, 0.2, 0.4, 0.6):
        gw, hw, qe = [], [], []
        for _ in range(n):
            rng = np.random.default_rng(int(master.integers(0, 2**31)))
            gen = DataGenerator(rng)
            apps = gen.generate_applicants(N, {})
            gamers = set()
            score = {}
            for a in apps:
                is_gamer = rng.random() < frac
                if is_gamer:
                    gamers.add(a.id)
                # visible portion: gamer inflates; confidential: true quality only
                visible = a.true_quality + (boost if is_gamer else 0.0) + rng.normal(0, 0.4)
                confidential = a.true_quality + rng.normal(0, 0.4)
                score[a.id] = float(np.clip(
                    (1 - cw) * visible + cw * confidential, 0, 10))
            funded_set = set(sorted(score, key=lambda x: score[x], reverse=True)[:BUDGET])
            honest_ids = [a.id for a in apps if a.id not in gamers]
            if gamers:
                gw.append(len(funded_set & gamers) / len(gamers))
            if honest_ids:
                hw.append(len(funded_set & set(honest_ids)) / len(honest_ids))
            tq = {a.id: a.true_quality for a in apps}
            opt = sum(sorted(tq.values(), reverse=True)[:BUDGET])
            qe.append(sum(tq[i] for i in funded_set) / opt if opt else 0)
        print(f"  {cw:>12.1f}{mean(gw):>16.1%}{mean(hw):>17.1%}{mean(qe):>13.3f}")
    print("\n  READING: conf weight 0.0 = current (gamer wins big). As it rises the")
    print("  gamer win rate should fall toward the honest rate while quality")
    print("  efficiency RECOVERS - the un-gameable 40% restores merit.")


# ════════════════════════════════════════════════════════════════════════════
#  D3  VALIDATED COUPLING + FALSE-POSITIVE HARM
# ════════════════════════════════════════════════════════════════════════════
def d3_fp_harm(n=300):
    print("\n" + "=" * 80)
    print("D3  FALSE-POSITIVE HARM OF CARA ON CLEAN DATA")
    print("=" * 80)
    print(f"\n  Couple CARA into aggregation only if it doesn't punish honest panels.")
    print(f"  On clean data: how often does CARA trim, and does it hurt quality?\n")
    master = np.random.default_rng(SEED + 5)
    for k in (5, 7):
        trims, qe_krum, qe_cara, qe_mean = [], [], [], []
        for _ in range(n):
            rng = np.random.default_rng(int(master.integers(0, 2**31)))
            gen = DataGenerator(rng)
            apps = gen.generate_applicants(N, {})
            revs = gen.generate_reviewers(M, {})
            rmap = {r.id: r for r in revs}
            tq = {a.id: a.true_quality for a in apps}
            scores = {}
            for a in apps:
                rids = rng.choice(M, k, replace=False).tolist()
                scores[a.id] = {rid: float(np.clip(
                    a.true_quality + rmap[rid].bias + rng.normal(0, rmap[rid].noise_level),
                    0, 10)) for rid in rids}
            # how often does CARA's gap-trim fire on clean panels?
            for rs in scores.values():
                v = sorted(rs.values())
                if len(v) > 2:
                    g = max(v[i+1]-v[i] for i in range(len(v)-1))
                    trims.append(1.0 if g > 1.2 else 0.0)
            opt = sum(sorted(tq.values(), reverse=True)[:BUDGET])
            for fn, store in ((krum_scalar, qe_krum), (cara_scalar, qe_cara),
                              (lambda x: float(np.mean(x)), qe_mean)):
                fs = funded(scores, fn, k)
                store.append(sum(tq[i] for i in fs) / opt if opt else 0)
        print(f"  k={k}:  CARA trim-fires on {mean(trims):>5.1%} of clean panels  |  "
              f"quality eff -> mean {mean(qe_mean):.3f}  krum {mean(qe_krum):.3f}  "
              f"cara {mean(qe_cara):.3f}")
    print("\n  READING: if CARA's quality efficiency on clean data is >= krum's and")
    print("  trim-fire rate is modest, coupling it is safe (no honest-excellence")
    print("  penalty). If it trims often and quality drops, it's NOT safe to couple.")


if __name__ == '__main__':
    t0 = time.time()
    d1_anticollusion(n=120)
    d2_confidential(n=200)
    d3_fp_harm(n=300)
    print(f"\nAll V8 hardening tests complete in {time.time()-t0:.1f}s")
