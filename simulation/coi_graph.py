#!/usr/bin/env python3
"""
coi_graph.py — the REAL conflict-of-interest graph interface.

The simulator's `generate_network()` invents a random graph. That is a synthetic
stand-in. In a real deployment the COI graph is *sourced*, not invented — from
disclosure forms, corporate registries, co-authorship databases, employment
history, etc. This module is that interface: it loads a graph from CSV, answers
"is reviewer R in conflict with applicant A?", and runs a COI-respecting
assignment.

Crucially it makes the load-bearing weakness visible: every edge carries a
`provenance` and a `disclosed` flag, so you can ask the SAME graph under different
data-coverage regimes and watch conflicts appear and disappear. Some ties are
catchable only if you have registry data; some (undisclosed) are catchable by
nothing — those are the "hidden ties" the whole audit is about.

Data format (see ../data/example_coi_graph/):
  nodes.csv : node_id,label,type,org        type in {reviewer,applicant,person,org}
  edges.csv : src,dst,relation,provenance,disclosed,note

Run:  python coi_graph.py            (loads the bundled example and demos it)
"""

import csv, os, sys, argparse
from collections import defaultdict
import networkx as nx

DEFAULT_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'example_coi_graph')

# Data-coverage regimes: which edges you can actually SEE.
#   disclosure_only : only what reviewers self-report (the common real baseline)
#   external_data   : disclosures + external datasets (registries, ORCID, LinkedIn...)
# Neither can see a provenance=='undisclosed' tie — that is the permanent blind spot.
REGIMES = {
    'disclosure_only': lambda e: e['disclosed'],
    'external_data':   lambda e: e['provenance'] != 'undisclosed',
}


class COIGraph:
    def __init__(self, nodes, edges):
        self.nodes = {n['node_id']: n for n in nodes}
        self.edges = edges
        self.reviewers = [n for n in nodes if n['type'] == 'reviewer']
        self.applicants = [n for n in nodes if n['type'] == 'applicant']

    # -- construction --------------------------------------------------------
    @classmethod
    def load(cls, directory=DEFAULT_DIR):
        def read(name):
            with open(os.path.join(directory, name), newline='', encoding='utf-8') as f:
                return list(csv.DictReader(f))
        nodes = read('nodes.csv')
        edges = read('edges.csv')
        for e in edges:
            e['disclosed'] = str(e.get('disclosed', '')).strip().lower() == 'true'
        return cls(nodes, edges)

    def _graph_for(self, regime):
        """Build the undirected graph visible under a given data-coverage regime."""
        keep = REGIMES[regime]
        G = nx.Graph()
        G.add_nodes_from(self.nodes)
        for e in self.edges:
            if keep(e):
                G.add_edge(e['src'], e['dst'], relation=e['relation'],
                           provenance=e['provenance'])
        return G

    # -- queries -------------------------------------------------------------
    def conflict(self, reviewer, applicant, max_hops=2, regime='external_data'):
        """Return (in_conflict, hops, relation_chain) for one reviewer/applicant."""
        G = self._graph_for(regime)
        if reviewer not in G or applicant not in G:
            return (False, None, [])
        try:
            path = nx.shortest_path(G, reviewer, applicant)
        except nx.NetworkXNoPath:
            return (False, None, [])
        hops = len(path) - 1
        if hops > max_hops:
            return (False, None, [])
        chain = [G.edges[path[i], path[i + 1]]['relation'] for i in range(len(path) - 1)]
        return (True, hops, chain)

    def eligible_reviewers(self, applicant, max_hops=2, regime='external_data'):
        return [r['node_id'] for r in self.reviewers
                if not self.conflict(r['node_id'], applicant, max_hops, regime)[0]]

    def true_conflicts(self, max_hops=2):
        """Ground truth: every conflict that ACTUALLY exists (uses all edges)."""
        out = []
        for r in self.reviewers:
            for a in self.applicants:
                hit, hops, chain = self.conflict(r['node_id'], a['node_id'],
                                                 max_hops, 'all_edges_truth')
                if hit:
                    out.append((r['node_id'], a['node_id'], hops, chain))
        return out


# add a hidden "ground truth" regime that sees everything, for auditing coverage
REGIMES['all_edges_truth'] = lambda e: True


# ── COI-respecting assignment (what the pipeline should call) ────────────────
def assign_with_coi(graph, k=3, max_hops=2, regime='external_data', rng=None):
    """Assign k reviewers to each applicant, excluding visible conflicts.
    Returns {applicant_id: [reviewer_ids]}. Falls back gracefully if too few
    eligible reviewers remain (and reports it)."""
    import random
    rng = rng or random.Random(42)
    assignment, shortfalls = {}, []
    for a in graph.applicants:
        elig = graph.eligible_reviewers(a['node_id'], max_hops, regime)
        if len(elig) >= k:
            assignment[a['node_id']] = rng.sample(elig, k)
        else:
            assignment[a['node_id']] = elig[:]           # everyone eligible
            shortfalls.append((a['node_id'], len(elig)))
    return assignment, shortfalls


# ── demo ─────────────────────────────────────────────────────────────────────
def _name(g, nid):
    n = g.nodes[nid]
    return f"{n['label']}" + (f" ({n['org']})" if n.get('org') else "")


def demo(directory=DEFAULT_DIR, k=3, max_hops=2):
    g = COIGraph.load(directory)
    print("=" * 78)
    print("EXAMPLE COI GRAPH  —  research-grant round")
    print("=" * 78)
    print(f"  {len(g.reviewers)} reviewers, {len(g.applicants)} applicants, "
          f"{len(g.edges)} relationship edges. max_hops={max_hops}\n")

    # 1) ground-truth conflicts (all edges, including undisclosed)
    truth = {(r, a) for r, a, _, _ in g.true_conflicts(max_hops)}
    print("  TRUE conflicts that exist in reality:")
    for r, a, hops, chain in g.true_conflicts(max_hops):
        print(f"    {r}~{a}  ({hops}-hop: {'/'.join(chain)})   "
              f"{_name(g,r)}  <->  {_name(g,a)}")

    # 2) what each data-coverage regime actually catches
    for regime in ('disclosure_only', 'external_data'):
        caught = set()
        for r in g.reviewers:
            for a in g.applicants:
                if g.conflict(r['node_id'], a['node_id'], max_hops, regime)[0]:
                    caught.add((r['node_id'], a['node_id']))
        missed = truth - caught
        print(f"\n  Regime '{regime}': catches {len(caught)}/{len(truth)} true conflicts.")
        for (r, a) in sorted(missed):
            e = next((x for x in g.edges if {x['src'], x['dst']} == {r, a}), None)
            why = f"provenance={e['provenance']}, disclosed={e['disclosed']}" if e else "indirect"
            print(f"    MISSED {r}~{a}  ({why})  -> would be wrongly assignable")

    # 3) an actual assignment under the realistic 'external_data' regime
    print(f"\n  COI-respecting assignment (regime='external_data', k={k}):")
    assign, short = assign_with_coi(g, k=k, max_hops=max_hops, regime='external_data')
    for a in g.applicants:
        revs = assign[a['node_id']]
        tags = []
        for r in revs:
            if (r, a['node_id']) in truth:       # a real conflict slipped through
                tags.append(f"{r}!")
            else:
                tags.append(r)
        flag = "   <-- CONTAINS UNDETECTED CONFLICT" if any('!' in t for t in tags) else ""
        print(f"    {a['node_id']} {_name(g, a['node_id']):32} <- {', '.join(tags)}{flag}")

    print("\n  READING: the 'external_data' regime still can't see the undisclosed")
    print("  sibling tie (R6~A1), so R6 remains assignable to A1 and any assignment")
    print("  that picks R6 for A1 seats a real conflict the system will never flag.")
    print("  That single invisible edge is the entire audit thesis in miniature.")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=DEFAULT_DIR)
    ap.add_argument('--k', type=int, default=3)
    ap.add_argument('--max-hops', type=int, default=2)
    args = ap.parse_args()
    demo(args.dir, k=args.k, max_hops=args.max_hops)
