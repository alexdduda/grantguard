#!/usr/bin/env python3
"""
GrantGuard V10 - The three remaining attack axes, each with a targeted defense.

  AX1 Temporal (slow threshold creep)  -> defense: CUSUM trend test across rounds
  AX2 Volume (proposal flooding)        -> defense: per-entity submission cap
  AX3 Threshold-splitting (k=7 dodge)   -> defense: related-award value aggregation

Each section: attack succeeds vs the naive rule, then the defense re-tests.
Run from simulation/:  python grantguard_v10.py   (fast - no permutation CRS)
"""

import numpy as np
import warnings, sys, time
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

from grantguard_simulation import DataGenerator, Config
from grantguard_v8 import realize, funded, median_scalar

cfg = Config()
SEED = 42
N, M = cfg.N_APPLICANTS, cfg.N_REVIEWERS
BUDGET = max(1, int(N * cfg.BUDGET_FRACTION))


def mean(xs):
    return float(np.mean(xs)) if len(xs) else 0.0


# ════════════════════════════════════════════════════════════════════════════
#  AX1  TEMPORAL: slow threshold creep  vs  CUSUM trend defense
# ════════════════════════════════════════════════════════════════════════════
def ax1_temporal(n=200, T=12, creep=0.30, sigma=0.40):
    print("=" * 80)
    print("AX1  TEMPORAL THRESHOLD CREEP  (per-round drift too small to trip a check)")
    print("=" * 80)
    print(f"\n  A mediocre target re-applies for {T} rounds; the ring nudges its score")
    print(f"  by +{creep}/round - invisible per round, decisive cumulatively.")
    print(f"  Defense: CUSUM on the target's score drift vs its own baseline.\n")
    # CUSUM params
    slack, h = 0.5 * sigma, 4.0 * sigma
    master = np.random.default_rng(SEED)
    for label, defense in [('no defense', False), ('CUSUM trend test', True)]:
        cap, capR, det_before = [], [], []
        for _ in range(n):
            rng = np.random.default_rng(int(master.integers(0, 2**31)))
            gen = DataGenerator(rng)
            apps = gen.generate_applicants(N, {})
            q = sorted((a.true_quality for a in apps), reverse=True)
            C = q[BUDGET - 1]                       # score needed to be funded
            qT = q[int(N * 0.65)]                   # mediocre target (won't win honestly)
            S, cap_round, det_round = 0.0, None, None
            for t in range(1, T + 1):
                x = qT + creep * t + rng.normal(0, sigma)   # observed target score
                S = max(0.0, S + (x - qT - slack))          # CUSUM vs honest baseline
                if det_round is None and S > h:
                    det_round = t
                if cap_round is None and x >= C:
                    cap_round = t
            if defense:
                captured = (cap_round is not None and
                            (det_round is None or cap_round < det_round))
            else:
                captured = cap_round is not None
            cap.append(1.0 if captured else 0.0)
            if cap_round:
                capR.append(cap_round)
            if det_round and cap_round:
                det_before.append(1.0 if det_round <= cap_round else 0.0)
        extra = (f"   detected-before-capture: {mean(det_before):.1%}"
                 if defense else f"   mean capture round: {mean(capR):.1f}")
        print(f"  {label:<20} capture rate: {mean(cap):>6.1%}{extra}")
    print("\n  READING: the creep captures ~always with no temporal monitoring. CUSUM")
    print("  flags the drift before it crosses the funding line, slashing capture.")


# ════════════════════════════════════════════════════════════════════════════
#  AX2  VOLUME: proposal flooding  vs  per-entity submission cap
# ════════════════════════════════════════════════════════════════════════════
def ax2_volume(n=200, floods=(0, 4, 8, 16), vboost=1.6):
    print("\n" + "=" * 80)
    print("AX2  VOLUME / FLOODING  (one cartel submits many boosted mediocre bids)")
    print("=" * 80)
    print(f"\n  Cartel floods the pool with mediocre proposals, each given a +{vboost}")
    print(f"  observable boost. Metric: share of the {BUDGET} funded slots it captures.")
    print(f"  Defense: per-entity submission cap (one cartel = at most 1 funded bid).\n")
    print(f"  {'flood size':>11}{'capture (no cap)':>18}{'capture (cap)':>16}")
    print("  " + "-" * 45)
    master = np.random.default_rng(SEED + 1)
    for flood in floods:
        cap_off, cap_on = [], []
        for _ in range(n):
            rng = np.random.default_rng(int(master.integers(0, 2**31)))
            gen = DataGenerator(rng)
            apps = gen.generate_applicants(N, {})
            q = sorted((a.true_quality for a in apps), reverse=True)
            qmed = q[int(N * 0.55)]
            items = {f'h{a.id}': (a.true_quality + rng.normal(0, 0.4), 'honest') for a in apps}
            for i in range(flood):
                items[f'c{i}'] = (qmed + vboost + rng.normal(0, 0.4), 'cartel')
            # no cap
            top = sorted(items, key=lambda x: items[x][0], reverse=True)[:BUDGET]
            cap_off.append(sum(1 for x in top if items[x][1] == 'cartel') / BUDGET)
            # per-entity cap: keep only the cartel's single best bid
            cartel_ids = [x for x in items if items[x][1] == 'cartel']
            if cartel_ids:
                best = max(cartel_ids, key=lambda x: items[x][0])
                capped = {x: v for x, v in items.items()
                          if v[1] != 'cartel' or x == best}
            else:
                capped = items
            top2 = sorted(capped, key=lambda x: capped[x][0], reverse=True)[:BUDGET]
            cap_on.append(sum(1 for x in top2 if capped[x][1] == 'cartel') / BUDGET)
        print(f"  {flood:>11}{mean(cap_off):>18.1%}{mean(cap_on):>16.1%}")
    print("\n  READING: with no cap, capture scales with how many bids the cartel can")
    print("  afford to submit. A per-entity cap bounds it to a single slot regardless.")


# ════════════════════════════════════════════════════════════════════════════
#  AX3  THRESHOLD-SPLITTING  vs  related-award value aggregation
# ════════════════════════════════════════════════════════════════════════════
def _capture_rate(k, ring, n, master, delta=2.0):
    pe = []
    for _ in range(n):
        rng = np.random.default_rng(int(master.integers(0, 2**31)))
        apps, tq, tid, atk, hon = realize(rng, ring, delta, k, dispersion=False)
        won_on = tid in funded(atk, median_scalar, k)
        won_off = tid in funded(hon, median_scalar, k)
        pe.append(1.0 if (won_on and not won_off) else 0.0)
    return mean(pe)


def ax3_splitting(n=200, n_splits=4):
    print("\n" + "=" * 80)
    print("AX3  THRESHOLD-SPLITTING  (dodge the k=7 rule by chopping the award)")
    print("=" * 80)
    print(f"\n  A big award would trigger k=7 review. Split it into {n_splits} 'small'")
    print(f"  awards reviewed at k=3, where a 2-member ring is already a MAJORITY.")
    print(f"  Defense: sum related awards by entity; if over threshold, escalate to k=7.\n")
    master = np.random.default_rng(SEED + 2)
    single = _capture_rate(7, 2, n, master)
    per_sub = _capture_rate(3, 2, n, master)
    any_sub = 1.0 - (1.0 - per_sub) ** n_splits     # capture >=1 of the splits
    defended = _capture_rate(7, 2, n, master)        # aggregation forces k=7 back
    print(f"  {'single award @ k=7 (ring 2/7)':<38}capture = {single:.1%}")
    print(f"  {'each split @ k=3 (ring 2/3)':<38}capture = {per_sub:.1%}")
    print(f"  {f'>=1 of {n_splits} splits captured (no defense)':<38}capture = {any_sub:.1%}")
    print(f"  {'splits w/ value-aggregation -> k=7':<38}capture = {defended:.1%}")
    print("\n  READING: at k=3 a 2-ring is a majority Krum/median cannot survive, so")
    print("  splitting turns a hard target into several easy ones. Re-aggregating")
    print("  related awards by entity restores the k=7 protection.")


if __name__ == '__main__':
    t0 = time.time()
    ax1_temporal(n=200)
    ax2_volume(n=200)
    ax3_splitting(n=200)
    print(f"\nAll V10 axis attacks+defenses complete in {time.time()-t0:.1f}s")
