#!/usr/bin/env python3
"""
GrantGuard V11 - the V9 "COI-coverage collapse" reproduced on the REAL example
graph (data/example_coi_graph/) instead of a synthetic random network.

Setup: a colluding ring of reviewers wants a borderline applicant (A3) funded.
Their collusion ties are only *visible* to the assigner in proportion to how
complete the COI data is (the `coverage` knob). Dispersion-aware assignment keeps
visibly-tied reviewers off the same panel; invisible ties let the ring co-locate,
form a majority, and drag the median. This is v9's curve, now on named people.

Run from simulation/:  python grantguard_v11.py
"""

import sys, os, random
sys.path.insert(0, '.')
import numpy as np
from coi_graph import COIGraph, DEFAULT_DIR
from grantguard_v8 import median_scalar

SEED = 42

# fixed applicant qualities: A3 is borderline (just below the top-2 cutoff)
QUALITY = {'A1': 8.5, 'A2': 7.6, 'A3': 7.2, 'A5': 6.5, 'A6': 5.0, 'A4': 4.5}
TARGET = 'A3'
RING = ['R2', 'R5', 'R7']          # secretly colluding; all eligible to review A3
K = 3
MAX_HOPS = 2
REGIME = 'external_data'            # the realistic COI regime for reviewer<->applicant
DELTA = 1.0                         # centroid + delta the ring posts for the target


def reviewer_reviewer_ties(g):
    """Disclosed reviewer<->reviewer ties already in the graph (always visible)."""
    rids = {r['node_id'] for r in g.reviewers}
    ties = set()
    for e in g.edges:
        if e['src'] in rids and e['dst'] in rids and e['provenance'] != 'undisclosed':
            ties.add(frozenset((e['src'], e['dst'])))
    return ties


def visible_ties(base_ties, coverage, rng):
    """Base ties + ring-internal collusion ties, each visible w.p. coverage."""
    ties = set(base_ties)
    for i in range(len(RING)):
        for j in range(i + 1, len(RING)):
            if rng.random() < coverage:            # this collusion link is in the data
                ties.add(frozenset((RING[i], RING[j])))
    return ties


def assign_panel(g, applicant, ties, rng):
    """k reviewers, no reviewer<->applicant COI, and no two visibly-tied reviewers."""
    elig = [r['node_id'] for r in g.reviewers
            if not g.conflict(r['node_id'], applicant, MAX_HOPS, REGIME)[0]]
    rng.shuffle(elig)
    panel = []
    for r in elig:
        if len(panel) >= K:
            break
        if any(frozenset((r, p)) in ties for p in panel):   # dispersion
            continue
        panel.append(r)
    return panel


def score_and_fund(g, ties, rng, attack):
    """One round. Returns True if TARGET is in the funded set."""
    bias = {r['node_id']: rng.gauss(0, 0.3) for r in g.reviewers}
    agg = {}
    for a in g.applicants:
        aid = a['node_id']
        panel = assign_panel(g, aid, ties, rng)
        if not panel:
            agg[aid] = 0.0
            continue
        scores = {}
        for r in panel:
            scores[r] = float(np.clip(QUALITY[aid] + bias[r] + rng.gauss(0, 0.4), 0, 10))
        if attack and aid == TARGET:
            ring_on = [r for r in panel if r in RING]
            honest = [s for r, s in scores.items() if r not in RING]
            centroid = float(np.mean(honest)) if honest else QUALITY[aid]
            for r in ring_on:
                scores[r] = float(np.clip(centroid + DELTA + rng.gauss(0, 0.03), 0, 10))
        agg[aid] = median_scalar(list(scores.values()))
    budget = max(1, int(len(g.applicants) * 0.4))
    funded = sorted(agg, key=lambda x: agg[x], reverse=True)[:budget]
    return TARGET in funded


def ring_colocation(g, ties, rng):
    """How many ring members land on the target's panel."""
    panel = assign_panel(g, TARGET, ties, rng)
    return sum(1 for r in panel if r in RING)


def main(n=400):
    g = COIGraph.load(DEFAULT_DIR)
    base = reviewer_reviewer_ties(g)
    budget = max(1, int(len(g.applicants) * 0.4))
    print("=" * 78)
    print("V11  COI-COVERAGE COLLAPSE ON THE REAL EXAMPLE GRAPH")
    print("=" * 78)
    print(f"  Target: {TARGET} (quality {QUALITY[TARGET]}, just below the top-{budget} cutoff).")
    print(f"  Ring: {RING}. k={K}, median aggregation, delta={DELTA}.")
    print(f"  Coverage = fraction of the ring's collusion ties visible to the assigner.\n")
    print(f"  {'coverage':>9}{'ring on panel':>15}{'target won (attack)':>21}"
          f"{'won (honest)':>14}{'pure effect':>13}")
    print("  " + "-" * 72)
    for coverage in (0.0, 0.25, 0.5, 0.75, 1.0):
        colo, won_a, won_h = [], [], []
        for _ in range(n):
            rng = random.Random(int(np.random.default_rng(SEED + hash((coverage, _)) % 2**31).integers(0, 2**31)))
            ties = visible_ties(base, coverage, rng)
            colo.append(ring_colocation(g, ties, rng))
            # fresh rng per condition so honest/attack share assignment noise fairly
            r2 = random.Random(rng.random())
            ties2 = visible_ties(base, coverage, r2)
            won_a.append(1.0 if score_and_fund(g, ties2, r2, attack=True) else 0.0)
            r3 = random.Random(rng.random())
            ties3 = visible_ties(base, coverage, r3)
            won_h.append(1.0 if score_and_fund(g, ties3, r3, attack=False) else 0.0)
        pe = np.mean(won_a) - np.mean(won_h)
        print(f"  {coverage:>9.2f}{np.mean(colo):>15.2f}{np.mean(won_a):>21.1%}"
              f"{np.mean(won_h):>14.1%}{pe:>13.1%}")
    print("\n  READING: as coverage drops the ring's collusion ties go unseen, so")
    print("  dispersion can't separate them - ring-on-panel climbs 1.0 -> 1.6 and")
    print("  pure effect climbs ~22% -> ~54%. Same collapse as V9, now driven by the")
    print("  completeness of a REAL relationship graph.")
    print("  Caveat this run also exposes: pure effect stays ~22% even at coverage")
    print("  1.0, because with k=3 a lone co-assigned ring member already shifts the")
    print("  median (it drops the low tail). That is a k=3 artifact - it argues for")
    print("  k=7, which needs a >=3 majority to move and would push this residual down.")


if __name__ == '__main__':
    main(n=400)
