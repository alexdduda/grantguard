# Example COI Graph

A small, concrete conflict-of-interest graph for one research-grant round —
the kind of relationship data a real GrantGuard deployment must assemble and that
the simulator's `generate_network()` only fakes. Load and explore it with
[`simulation/coi_graph.py`](../../simulation/coi_graph.py):

```bash
python simulation/coi_graph.py
```

## The scenario

7 expert reviewers, 6 applicants (each a PI + their org), and 10 relationship
edges drawn from the sources a real programme would have. The point of the example
is the **provenance** column: it lets you ask the same graph "what could we see if
we only had X data?" and watch conflicts appear and disappear.

## Files / schema

**`nodes.csv`** — `node_id, label, type, org`
`type` ∈ `reviewer | applicant | person | org`. `person`/`org` are *intermediary*
nodes (a shared co-author, a former employer) that create 2–3-hop conflicts.

**`edges.csv`** — `src, dst, relation, provenance, disclosed, note`
- `relation` — coauthor, phd_advisor, former_employee, board_member, sibling, …
- `provenance` — where this tie *would* be found: `orcid`, `linkedin`,
  `university_records`, `corporate_registry`, or `undisclosed` (found nowhere).
- `disclosed` — whether the reviewer actually reported it on their form.

## The three tiers of visibility (the whole point)

| tie | provenance | seen by `disclosure_only`? | seen by `external_data`? | reality |
|---|---|---|---|---|
| R1~A5, R3~A2, R2~A1, R4~A3 … | orcid / registry / disclosed | ✅ | ✅ | caught |
| **R5~A6** (board seat) | corporate_registry, **not disclosed** | ❌ | ✅ | caught only if you pull registry data |
| **R6~A1** (siblings) | **undisclosed** | ❌ | ❌ | **never caught** — the permanent blind spot |

Running the demo shows `disclosure_only` catching 6/8 true conflicts,
`external_data` catching 7/8, and R6~A1 slipping through every regime — so an
otherwise-valid assignment seats Dr. Silva on her own brother's panel and the
system never flags it. That single invisible edge is the audit's entire thesis
(see [AUDIT_FINDINGS.md](../../AUDIT_FINDINGS.md)) made concrete.

## Using your own data

Replace these two CSVs with your programme's real graph (same columns), or point
the loader elsewhere: `python simulation/coi_graph.py --dir path/to/your/graph`.
`coi_graph.assign_with_coi(...)` is the interface the allocation pipeline should
call in place of the synthetic `generate_network()`.
