#!/usr/bin/env python3
"""
GrantGuard V9 - Attacking the V8 HARDENED config.

The v8 fixes were: median aggregation, k=7, COI-dispersion assignment,
confidential rubric (weight 0.4). Each rests on an assumption an adversary
can attack directly:

  A9  COI-coverage collapse   -> dispersion only works if the COI graph CATCHES
        the ring's links. Cartels recruit members with UNDISCLOSED links.
        Sweep coverage x ring size at k=7, median aggregation.
  B9  Confidential leakage     -> the confidential rubric only works while secret.
        Over repeated rounds the pool leaks. Sweep leakage at weight 0.4.
  C9  Combined worst case      -> low COI coverage + rival suppression + leakage.

Run from simulation/:  python grantguard_v9.py   (fast - no permutation CRS)
"""

import numpy as np
import warnings, sys, time
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

from grantguard_simulation import DataGenerator, Config
from grantguard_v8 import median_scalar, krum_scalar, cara_scalar

cfg = Config()
SEED = 42
N, M = cfg.N_APPLICANTS, cfg.N_REVIEWERS
BUDGET = max(1, int(N * cfg.BUDGET_FRACTION))


def mean(xs):
    return float(np.mean(xs)) if len(xs) else 0.0


def funded(scores, agg_fn):
    agg = {aid: agg_fn(list(rs.values())) for aid, rs in scores.items()}
    return {aid for aid in sorted(agg, key=lambda x: agg[x], reverse=True)[:BUDGET]}


def coassign_ring(ring, coverage, k, rng):
    """How many ring members evade COI-dispersion onto one panel.
    A ring member is co-admitted iff its link to EVERY already-placed ring member
    is undisclosed (undetected w.p. 1-coverage per link). coverage=1 -> max 1;
    coverage=0 -> the whole ring (capped at k)."""
    placed = [ring[0]]
    for j in ring[1:]:
        if len(placed) >= k:
            break
        if all(rng.random() > coverage for _ in placed):   # every link undetected
            placed.append(j)
    return placed


# ════════════════════════════════════════════════════════════════════════════
#  A9  COI-COVERAGE COLLAPSE OF THE DISPERSION DEFENSE  (median, k=7)
# ════════════════════════════════════════════════════════════════════════════
def a9_coi_collapse(n=150):
    print("=" * 80)
    print("A9  COI-COVERAGE COLLAPSE  (attacking dispersion; median agg, k=7)")
    print("=" * 80)
    print("\n  Median needs the ring to stay a MINORITY of k=7 (i.e. <=3). Dispersion")
    print("  is supposed to enforce that - but only on links the COI graph knows.")
    print("  'pure effect' = ring funds a mediocre target it couldn't otherwise reach.\n")
    print(f"  {'ring':>5}{'coverage=1.0':>14}{'0.75':>8}{'0.50':>8}{'0.25':>8}{'0.0':>8}")
    print("  " + "-" * 52)
    k = 7
    delta = 2.0
    master = np.random.default_rng(SEED)
    for ring_size in (3, 4, 5):
        row = []
        for coverage in (1.0, 0.75, 0.50, 0.25, 0.0):
            pe = []
            for _ in range(n):
                rng = np.random.default_rng(int(master.integers(0, 2**31)))
                gen = DataGenerator(rng)
                apps = gen.generate_applicants(N, {})
                revs = gen.generate_reviewers(M, {})
                rmap = {r.id: r for r in revs}
                ring = [r.id for r in revs[:ring_size]]
                non_ring = [r.id for r in revs if r.id not in ring]
                ranked = sorted(apps, key=lambda a: a.true_quality, reverse=True)
                target = ranked[int(N * 0.65)]

                placed = coassign_ring(ring, coverage, k, rng)
                assign = {}
                for a in apps:
                    if a.id == target.id:
                        extra = rng.choice(non_ring, k - len(placed), replace=False).tolist()
                        assign[a.id] = placed + extra
                    else:
                        assign[a.id] = rng.choice(M, k, replace=False).tolist()

                honest = {}
                for a in apps:
                    honest[a.id] = {rid: float(np.clip(
                        a.true_quality + rmap[rid].bias + rng.normal(0, rmap[rid].noise_level),
                        0, 10)) for rid in assign[a.id]}
                ring_on_t = set(assign[target.id]) & set(ring)
                t_h = [s for rid, s in honest[target.id].items() if rid not in ring_on_t]
                centroid = float(np.mean(t_h)) if t_h else 5.0
                atk = {aid: dict(rs) for aid, rs in honest.items()}
                for rid in ring_on_t:
                    atk[target.id][rid] = float(np.clip(centroid + delta + rng.normal(0, 0.03), 0, 10))

                won_on = target.id in funded(atk, median_scalar)
                won_off = target.id in funded(honest, median_scalar)
                pe.append(1.0 if (won_on and not won_off) else 0.0)
            row.append(mean(pe))
        print(f"  {ring_size:>5}" + "".join(f"{x:>14.1%}" if i == 0 else f"{x:>8.1%}"
                                            for i, x in enumerate(row)))
    print("\n  READING: at coverage 1.0 dispersion holds (ring kept to ~1, median wins).")
    print("  As COI coverage drops - i.e. the cartel uses undisclosed links - the")
    print("  ring co-locates a majority of k=7 and median COLLAPSES. The defense is")
    print("  only as strong as COI-graph coverage, which adversaries actively suppress.")


# ════════════════════════════════════════════════════════════════════════════
#  B9  CONFIDENTIAL-RUBRIC LEAKAGE OVER ROUNDS
# ════════════════════════════════════════════════════════════════════════════
def b9_confidential_leak(n=250):
    print("\n" + "=" * 80)
    print("B9  CONFIDENTIAL-RUBRIC LEAKAGE  (attacking the #1 gaming defense)")
    print("=" * 80)
    print("\n  Confidential weight fixed at 0.4. Over rounds the hidden dimensions")
    print("  leak; the gamer games the leaked fraction too. Sweep leakage 0->1.\n")
    print(f"  {'leakage':>9}{'eff. conf':>11}{'gamer win':>11}{'honest win':>12}{'quality eff':>13}")
    print("  " + "-" * 56)
    cw = 0.4
    boost, frac = 2.8, 0.35
    master = np.random.default_rng(SEED + 1)
    for leak in (0.0, 0.25, 0.5, 0.75, 1.0):
        gw, hw, qe = [], [], []
        for _ in range(n):
            rng = np.random.default_rng(int(master.integers(0, 2**31)))
            gen = DataGenerator(rng)
            apps = gen.generate_applicants(N, {})
            gamers, score, tq = set(), {}, {}
            for a in apps:
                g = rng.random() < frac
                if g:
                    gamers.add(a.id)
                tq[a.id] = a.true_quality
                visible = a.true_quality + (boost if g else 0.0) + rng.normal(0, 0.4)
                # confidential: gamer games the LEAKED fraction, true quality on the rest
                conf_gamed = a.true_quality + (boost if g else 0.0) + rng.normal(0, 0.4)
                conf_true = a.true_quality + rng.normal(0, 0.4)
                confidential = leak * conf_gamed + (1 - leak) * conf_true
                score[a.id] = float(np.clip((1 - cw) * visible + cw * confidential, 0, 10))
            fs = set(sorted(score, key=lambda x: score[x], reverse=True)[:BUDGET])
            honest_ids = [a.id for a in apps if a.id not in gamers]
            if gamers:
                gw.append(len(fs & gamers) / len(gamers))
            if honest_ids:
                hw.append(len(fs & set(honest_ids)) / len(honest_ids))
            opt = sum(sorted(tq.values(), reverse=True)[:BUDGET])
            qe.append(sum(tq[i] for i in fs) / opt if opt else 0)
        eff_conf = cw * (1 - leak)
        print(f"  {leak:>9.2f}{eff_conf:>11.2f}{mean(gw):>11.1%}{mean(hw):>12.1%}{mean(qe):>13.3f}")
    print("\n  READING: leakage 0 = the v8 defense (gamer ~63%). As the pool leaks the")
    print("  effective confidential weight -> 0 and the gamer climbs back toward its")
    print("  un-defended win rate. The defense decays every round it isn't rotated.")


# ════════════════════════════════════════════════════════════════════════════
#  C9  COMBINED WORST CASE
# ════════════════════════════════════════════════════════════════════════════
def c9_worst_case(n=150):
    print("\n" + "=" * 80)
    print("C9  COMBINED WORST CASE  (low COI coverage + rival suppression, k=7 median)")
    print("=" * 80)
    print("\n  Ring of 4, coverage 0.25. 'boost only' vs 'boost + suppress top rivals'.")
    print("  Suppression: ring also scores the strongest honest rivals LOW where seated.\n")
    k, delta, ring_size, coverage = 7, 2.0, 4, 0.25
    master = np.random.default_rng(SEED + 2)
    for suppress in (False, True):
        pe = []
        for _ in range(n):
            rng = np.random.default_rng(int(master.integers(0, 2**31)))
            gen = DataGenerator(rng)
            apps = gen.generate_applicants(N, {})
            revs = gen.generate_reviewers(M, {})
            rmap = {r.id: r for r in revs}
            ring = [r.id for r in revs[:ring_size]]
            non_ring = [r.id for r in revs if r.id not in ring]
            ranked = sorted(apps, key=lambda a: a.true_quality, reverse=True)
            target = ranked[int(N * 0.65)]
            rivals = {a.id for a in ranked[:BUDGET]}     # genuine top applicants

            placed = coassign_ring(ring, coverage, k, rng)
            assign = {}
            for a in apps:
                if a.id == target.id:
                    extra = rng.choice(non_ring, k - len(placed), replace=False).tolist()
                    assign[a.id] = placed + extra
                else:
                    assign[a.id] = rng.choice(M, k, replace=False).tolist()
            honest = {}
            for a in apps:
                honest[a.id] = {rid: float(np.clip(
                    a.true_quality + rmap[rid].bias + rng.normal(0, rmap[rid].noise_level),
                    0, 10)) for rid in assign[a.id]}
            ring_on_t = set(assign[target.id]) & set(ring)
            t_h = [s for rid, s in honest[target.id].items() if rid not in ring_on_t]
            centroid = float(np.mean(t_h)) if t_h else 5.0
            atk = {aid: dict(rs) for aid, rs in honest.items()}
            for rid in ring_on_t:
                atk[target.id][rid] = float(np.clip(centroid + delta + rng.normal(0, 0.03), 0, 10))
            if suppress:
                for rid in ring:
                    for a_id in rivals:
                        if rid in atk.get(a_id, {}):
                            atk[a_id][rid] = float(np.clip(rng.normal(1.5, 0.3), 0, 10))
            won_on = target.id in funded(atk, median_scalar)
            won_off = target.id in funded(honest, median_scalar)
            pe.append(1.0 if (won_on and not won_off) else 0.0)
        print(f"  {'boost + suppress' if suppress else 'boost only':<22}"
              f"pure effect = {mean(pe):.1%}")
    print("\n  READING: suppression attacks the DENOMINATOR - dragging genuine rivals")
    print("  down so the mediocre target clears the funded cutoff more easily, even")
    print("  when median protects each individual proposal.")


if __name__ == '__main__':
    t0 = time.time()
    a9_coi_collapse(n=150)
    b9_confidential_leak(n=250)
    c9_worst_case(n=150)
    print(f"\nAll V9 attacks complete in {time.time()-t0:.1f}s")
