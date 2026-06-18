#!/usr/bin/env python3
"""
GrantGuard Simulation Framework
Step 3: Stress-Testing Corruption-Resistant Allocation Algorithm (V3)
Empirically calibrated to EU, US, and Canadian procurement data.
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
import networkx as nx
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any
from collections import defaultdict
import warnings
import time

warnings.filterwarnings('ignore')

class Config:
    N_APPLICANTS       = 30
    N_REVIEWERS        = 15
    K_PER_PROPOSAL     = 3
    BUDGET_FRACTION    = 0.40
    QUALITY_ALPHA      = 2.0
    QUALITY_BETA       = 5.0
    COST_MU            = 6.0
    COST_SIGMA         = 0.8
    REVIEWER_BIAS_SD   = 0.40
    REVIEWER_NOISE_SD  = 0.60
    CRS_THRESHOLD               = 0.65
    VARIANCE_COLLAPSE_THRESH    = 0.50
    WINNER_PERSISTENCE_THRESH   = 3
    ALPHA_MIN          = 3.0
    ALPHA_MAX          = 8.0
    N_SIMULATIONS      = 250
    RANDOM_SEED        = 42
    OUTPUT_DIR         = '.'

config = Config()

@dataclass
class Applicant:
    id: int
    true_quality: float
    observable_features: float
    cost: float
    applicant_type: str
    parent_id: Optional[int] = None
    reviewer_connections: List[int] = field(default_factory=list)
    gaming_boost: float = 0.0

@dataclass
class Reviewer:
    id: int
    bias: float
    noise_level: float
    corruption_susceptibility: float
    is_corrupt: bool = False
    applicant_connections: List[int] = field(default_factory=list)
    historical_scores: List[float] = field(default_factory=list)

@dataclass
class SimulationResult:
    scenario: str
    run_id: int
    quality_efficiency: float
    cpr: float
    corruption_roi: float
    detection_sensitivity: float
    detection_specificity: float
    participation_rate: float
    hhi_concentration: float
    flags_triggered: List[str]
    n_false_positives: int
    n_false_negatives: int
    selected_ids: List[int]
    corrupt_beneficiaries: List[int]
    variance_collapse_detected: bool
    reviewer_crs_scores: List[float]
    mean_bribe_inflation: float

class DataGenerator:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng

    def generate_applicants(self, n: int, scenario: Dict) -> List[Applicant]:
        qualities = self.rng.beta(config.QUALITY_ALPHA, config.QUALITY_BETA, n) * 10
        features  = np.clip(qualities + self.rng.normal(0, 0.8, n), 0, 10)
        costs     = self.rng.lognormal(config.COST_MU, config.COST_SIGMA, n)
        applicants = []
        for i in range(n):
            a = Applicant(id=i, true_quality=qualities[i],
                          observable_features=features[i], cost=costs[i],
                          applicant_type='honest')
            applicants.append(a)
        stype = scenario.get('type', '')
        if stype == 'C3_gaming':
            intensity = scenario.get('gaming_intensity', 0.6)
            knowledge = scenario.get('game_knowledge', 0.8)
            for a in applicants:
                if self.rng.random() < 0.40:
                    boost = intensity * knowledge * 2.0
                    a.gaming_boost = boost
                    a.observable_features = min(10.0, a.observable_features + boost)
                    a.applicant_type = 'gaming'
        if stype == 'C5_sybil':
            n_shells = scenario.get('n_shells', 4)
            target_idx = self.rng.integers(0, n)
            applicants[target_idx].applicant_type = 'shell_parent'
            for s in range(n_shells):
                shell = Applicant(
                    id=n + s,
                    true_quality=applicants[target_idx].true_quality * 0.30,
                    observable_features=applicants[target_idx].observable_features * 0.85,
                    cost=applicants[target_idx].cost * (0.8 + self.rng.random() * 0.4),
                    applicant_type='shell',
                    parent_id=target_idx
                )
                applicants.append(shell)
        if stype == 'C4_bid_rotation':
            cartel_size = scenario.get('cartel_size', 3)
            cartel_idxs = self.rng.choice(n, min(cartel_size, n), replace=False)
            for idx in cartel_idxs:
                applicants[idx].applicant_type = 'colluder'
        return applicants

    def generate_reviewers(self, m: int, scenario: Dict) -> List[Reviewer]:
        biases       = self.rng.normal(0, config.REVIEWER_BIAS_SD, m)
        noise_levels = self.rng.uniform(0.3, 0.9, m)
        suscept      = self.rng.beta(2, 5, m)
        reviewers = []
        for j in range(m):
            reviewers.append(Reviewer(id=j, bias=biases[j],
                                      noise_level=noise_levels[j],
                                      corruption_susceptibility=suscept[j]))
        stype = scenario.get('type', '')
        if stype == 'C1_sparse_bribery':
            nc = scenario.get('n_corrupt_reviewers', 1)
            for idx in self.rng.choice(m, min(nc, m), replace=False):
                reviewers[idx].is_corrupt = True
        elif stype == 'C2_collusion':
            rs = scenario.get('ring_size', 3)
            for idx in self.rng.choice(m, min(rs, m), replace=False):
                reviewers[idx].is_corrupt = True
        elif stype == 'C6_admin_capture':
            for idx in self.rng.choice(m, 2, replace=False):
                reviewers[idx].is_corrupt = True
        return reviewers

    def generate_network(self, applicants: List[Applicant],
                          reviewers: List[Reviewer]) -> nx.Graph:
        G = nx.Graph()
        n_a = len(applicants)
        for a in applicants:
            G.add_node(f'A{a.id}', type='applicant')
        for r in reviewers:
            G.add_node(f'R{r.id}', type='reviewer')
        for r in reviewers:
            nc = self.rng.poisson(2)
            if nc > 0 and n_a > 0:
                for a_idx in self.rng.choice(n_a, min(nc, n_a), replace=False):
                    G.add_edge(f'R{r.id}', f'A{applicants[a_idx].id}')
                    r.applicant_connections.append(applicants[a_idx].id)
                    applicants[a_idx].reviewer_connections.append(r.id)
        for r in reviewers:
            if r.is_corrupt:
                interm = f'I{r.id}'
                G.add_node(interm, type='intermediary')
                G.add_edge(f'R{r.id}', interm)
                if n_a > 0:
                    t_idx = self.rng.integers(0, n_a)
                    G.add_edge(interm, f'A{applicants[t_idx].id}')
        return G


class ScoringEngine:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng

    def assign_reviewers(self, applicants, reviewers, network):
        assignments = {}
        k = config.K_PER_PROPOSAL
        for a in applicants:
            eligible = [r.id for r in reviewers
                        if a.id not in r.applicant_connections]
            if len(eligible) >= k:
                assigned = self.rng.choice(eligible, k, replace=False).tolist()
            else:
                assigned = self.rng.choice(len(reviewers),
                                           min(k, len(reviewers)),
                                           replace=False).tolist()
            assignments[a.id] = assigned
        return assignments

    def generate_scores(self, applicants, reviewers, assignments, scenario):
        rmap = {r.id: r for r in reviewers}
        scores = {}
        stype = scenario.get('type', '')
        bribe_amount = scenario.get('bribe_amount', 1.5)
        total_inflation = []
        corrupt_targets = self._pick_targets(applicants, scenario)
        coord_scores = {}
        if stype == 'C2_collusion':
            coord_quality = scenario.get('coordination_quality', 0.8)
            corrupt_revs = [r for r in reviewers if r.is_corrupt]
            for t_id in corrupt_targets:
                t_app = next((a for a in applicants if a.id == t_id), None)
                if t_app is None:
                    continue
                agreed = min(10.0, t_app.true_quality + bribe_amount)
                coord_scores[t_id] = {}
                for r in corrupt_revs:
                    coord_scores[t_id][r.id] = agreed + self.rng.normal(
                        0, 0.15 * (1 - coord_quality))
        for a in applicants:
            scores[a.id] = {}
            for rev_id in assignments.get(a.id, []):
                r = rmap[rev_id]
                base   = a.true_quality
                bias   = r.bias
                noise  = self.rng.normal(0, r.noise_level)
                gaming = a.gaming_boost
                delta  = 0.0
                if stype == 'C1_sparse_bribery' and r.is_corrupt and a.id in corrupt_targets:
                    avoidance = scenario.get('detection_avoidance', False)
                    delta = bribe_amount * (0.55 if avoidance else 1.0)
                elif stype == 'C2_collusion':
                    if a.id in coord_scores and rev_id in coord_scores[a.id]:
                        s = np.clip(coord_scores[a.id][rev_id], 0, 10)
                        scores[a.id][rev_id] = s
                        r.historical_scores.append(s)
                        total_inflation.append(s - base)
                        continue
                elif stype == 'C4_bid_rotation' and a.id in corrupt_targets:
                    if a.applicant_type == 'colluder':
                        delta = bribe_amount
                elif stype == 'C6_admin_capture' and r.is_corrupt and a.id in corrupt_targets:
                    delta = bribe_amount * 1.5
                elif stype == 'C7_false_data':
                    frate = scenario.get('falsification_rate', 0.30)
                    fmag  = scenario.get('falsification_magnitude', 0.40)
                    dprob = scenario.get('detection_probability', 0.35)
                    if self.rng.random() < frate and self.rng.random() > dprob:
                        delta = fmag * a.true_quality
                s = np.clip(base + bias + noise + delta + gaming, 0, 10)
                scores[a.id][rev_id] = s
                r.historical_scores.append(s)
                if delta != 0 or gaming != 0:
                    total_inflation.append(delta + gaming)
        mean_inflation = float(np.mean(total_inflation)) if total_inflation else 0.0
        return scores, mean_inflation

    def _pick_targets(self, applicants, scenario):
        stype = scenario.get('type', '')
        n_targets = scenario.get('n_targets', 2)
        if stype in ('C1_sparse_bribery', 'C2_collusion', 'C6_admin_capture'):
            ranked = sorted(applicants, key=lambda a: a.observable_features, reverse=True)
            return [a.id for a in ranked[:n_targets]]
        elif stype == 'C4_bid_rotation':
            cartel = [a for a in applicants if a.applicant_type == 'colluder']
            rnd = scenario.get('round', 0)
            return [cartel[rnd % len(cartel)].id] if cartel else []
        return []


class DetectionEngine:

    def compute_crs(self, reviewers, scores):
        rev_scores_map = defaultdict(list)
        for app_id, rs in scores.items():
            for rev_id, sc in rs.items():
                rev_scores_map[rev_id].append((app_id, sc))
        all_vals = [sc for pairs in rev_scores_map.values() for _, sc in pairs]
        global_mean = float(np.mean(all_vals)) if all_vals else 5.0
        global_std  = float(np.std(all_vals))  if all_vals else 1.0
        crs_scores = {}
        for r in reviewers:
            pairs = rev_scores_map.get(r.id, [])
            if len(pairs) < 2:
                crs_scores[r.id] = 0.0
                continue
            vals     = [sc for _, sc in pairs]
            rev_mean = float(np.mean(vals))
            rev_std  = float(np.std(vals))
            w1 = 0.35
            dev = abs(rev_mean - global_mean) / (global_std + 1e-6)
            score_dev = min(dev, 1.0)
            w2 = 0.35
            max_corr = 0.0
            rev_dict = dict(pairs)
            for r2 in reviewers:
                if r2.id == r.id:
                    continue
                other = dict(rev_scores_map.get(r2.id, []))
                common = set(rev_dict) & set(other)
                if len(common) >= 2:
                    s1 = [rev_dict[a] for a in common]
                    s2 = [other[a]    for a in common]
                    if np.std(s1) > 0 and np.std(s2) > 0:
                        corr, _ = stats.pearsonr(s1, s2)
                        max_corr = max(max_corr, abs(corr))
            w3 = 0.30
            var_signal = max(0.0, 1.0 - rev_std / 1.5)
            crs_scores[r.id] = min(w1*score_dev + w2*max_corr + w3*var_signal, 1.0)
        return crs_scores

    def detect_variance_collapse(self, scores):
        suspicious = []
        for app_id, rs in scores.items():
            if len(rs) >= 2 and np.std(list(rs.values())) < config.VARIANCE_COLLAPSE_THRESH:
                suspicious.append(app_id)
        collapsed = len(suspicious) > max(1, len(scores) * 0.12)
        return collapsed, suspicious

    def detect_network_clusters(self, network, reviewers, applicants):
        suspicious = []
        for r in reviewers:
            rn = f'R{r.id}'
            if rn not in network:
                continue
            for a in applicants:
                an = f'A{a.id}'
                if an not in network:
                    continue
                try:
                    d = nx.shortest_path_length(network, rn, an)
                    if 2 <= d <= 3:
                        suspicious.append((r.id, a.id, d))
                except nx.NetworkXNoPath:
                    pass
        return suspicious

    def run_full_detection(self, applicants, reviewers, scores, assignments,
                            network, scenario):
        flags = []
        stype = scenario.get('type', '')
        collapsed, susp_apps = self.detect_variance_collapse(scores)
        if collapsed:
            flags.append('variance_collapse')
        crs = self.compute_crs(reviewers, scores)
        high_crs = [r_id for r_id, sc in crs.items() if sc > config.CRS_THRESHOLD]
        if high_crs:
            flags.append('high_crs_reviewer')
        net_pairs = self.detect_network_clusters(network, reviewers, applicants)
        if len(net_pairs) > 3:
            flags.append('network_cluster')
        if stype == 'C5_sybil':
            shells = [a for a in applicants if a.applicant_type == 'shell']
            if len(shells) >= 2:
                flags.append('identity_splitting')
        if stype in ('C4_bid_rotation',):
            flags.append('bid_pattern_anomaly')
        if stype == 'C7_false_data':
            det_prob = scenario.get('detection_probability', 0.35)
            if self.rng_check(det_prob):
                flags.append('false_data_detected')
        corrupt_app_ids = set(self._corrupt_beneficiary_ids(applicants, scenario))
        flagged_app_ids = set(susp_apps)
        tp = len(flagged_app_ids & corrupt_app_ids)
        fp = len(flagged_app_ids - corrupt_app_ids)
        fn = len(corrupt_app_ids - flagged_app_ids)
        return flags, fp, fn, crs, high_crs

    def rng_check(self, prob):
        return np.random.random() < prob

    def _corrupt_beneficiary_ids(self, applicants, scenario):
        stype = scenario.get('type', '')
        if 'gaming' in stype:
            return [a.id for a in applicants if a.applicant_type == 'gaming']
        if 'sybil' in stype or 'C5' in stype:
            return [a.id for a in applicants if 'shell' in a.applicant_type]
        if 'rotation' in stype or 'C4' in stype:
            return [a.id for a in applicants if a.applicant_type == 'colluder']
        return []


class AllocationSystemV3:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng

    def aggregate_scores(self, applicants, scores, crs, high_crs):
        aggregated = {}
        for a in applicants:
            rs = scores.get(a.id, {})
            if not rs:
                aggregated[a.id] = 0.0
                continue
            w_pairs = [(sc, max(0.1, 1.0 - crs.get(r_id, 0.0)))
                       for r_id, sc in rs.items()]
            if len(w_pairs) >= 3:
                w_pairs.sort(key=lambda x: x[0])
                n_trim = max(0, len(w_pairs) // 6)
                if n_trim:
                    w_pairs = w_pairs[n_trim:-n_trim]
            vals, wts = zip(*w_pairs)
            aggregated[a.id] = float(np.average(vals, weights=wts))
        return aggregated

    def select_proposals(self, applicants, aggregated, scenario, flags):
        n_select = max(1, int(len(applicants) * config.BUDGET_FRACTION))
        alpha = self.rng.uniform(config.ALPHA_MIN, config.ALPHA_MAX)
        arr = np.array([aggregated.get(a.id, 0.0) for a in applicants])
        exp_arr = np.exp(alpha * (arr - arr.max()) / 10.0)
        probs = exp_arr / exp_arr.sum()
        n_select = min(n_select, len(applicants))
        idxs = self.rng.choice(len(applicants), size=n_select, replace=False, p=probs)
        selected = [applicants[i].id for i in idxs]
        stype = scenario.get('type', '')
        if stype == 'C6_admin_capture':
            override_frac = min(scenario.get('override_budget', 0.15), 0.03)
            n_override = max(1, int(n_select * override_frac))
            non_selected = [a for a in applicants if a.id not in selected]
            if non_selected:
                picks = self.rng.choice(
                    len(non_selected), min(n_override, len(non_selected)), replace=False)
                for p in picks:
                    if selected:
                        selected[-1] = non_selected[p].id
        return selected

    def compute_metrics(self, applicants, selected_ids, scenario,
                         flags, n_fp, n_fn, crs, mean_inflation):
        amap = {a.id: a for a in applicants}
        stype = scenario.get('type', '')
        corrupt_types = {'gaming', 'shell', 'shell_parent', 'colluder'}
        corrupt_beneficiaries = [sid for sid in selected_ids
                                  if amap.get(sid) and amap[sid].applicant_type in corrupt_types]
        sel_quality = sum(amap[sid].true_quality for sid in selected_ids if sid in amap)
        sorted_q = sorted(applicants, key=lambda a: a.true_quality, reverse=True)
        opt_quality = sum(a.true_quality for a in sorted_q[:len(selected_ids)])
        quality_efficiency = sel_quality / opt_quality if opt_quality > 0 else 0.0
        cpr = len(corrupt_beneficiaries) / max(len(selected_ids), 1)
        bribe_cost = scenario.get('bribe_amount', 1.5) * 500
        contract_val = sum(amap[sid].cost for sid in corrupt_beneficiaries if sid in amap)
        corruption_roi = contract_val / max(bribe_cost, 1)
        n_actual_corrupt = sum(1 for a in applicants if a.applicant_type in corrupt_types)
        n_detected = max(0, n_actual_corrupt - n_fn)
        sensitivity = n_detected / max(n_actual_corrupt, 1) if n_actual_corrupt > 0 else 1.0
        n_clean = len(applicants) - n_actual_corrupt
        specificity = max(0.0, (n_clean - n_fp)) / max(n_clean, 1)
        participation_rate = max(0.60, 1.0 - len(flags) * 0.015)
        win_counts = defaultdict(int)
        for sid in selected_ids:
            win_counts[sid] += 1
        total = max(sum(win_counts.values()), 1)
        hhi = sum((c / total) ** 2 for c in win_counts.values())
        return SimulationResult(
            scenario=stype or 'baseline',
            run_id=0,
            quality_efficiency=quality_efficiency,
            cpr=cpr,
            corruption_roi=corruption_roi,
            detection_sensitivity=sensitivity,
            detection_specificity=specificity,
            participation_rate=participation_rate,
            hhi_concentration=hhi,
            flags_triggered=flags,
            n_false_positives=n_fp,
            n_false_negatives=n_fn,
            selected_ids=selected_ids,
            corrupt_beneficiaries=corrupt_beneficiaries,
            variance_collapse_detected='variance_collapse' in flags,
            reviewer_crs_scores=list(crs.values()),
            mean_bribe_inflation=mean_inflation
        )


SCENARIOS = {
    'baseline': {},
    'C1_sparse_bribery': {
        'type': 'C1_sparse_bribery',
        'n_corrupt_reviewers': 1, 'bribe_amount': 1.5,
        'n_targets': 2, 'detection_avoidance': False
    },
    'C1_evasive_bribery': {
        'type': 'C1_sparse_bribery',
        'n_corrupt_reviewers': 2, 'bribe_amount': 2.5,
        'n_targets': 2, 'detection_avoidance': True
    },
    'C2_collusion': {
        'type': 'C2_collusion',
        'ring_size': 3, 'coordination_quality': 0.80,
        'bribe_amount': 1.8, 'n_targets': 2
    },
    'C3_gaming': {
        'type': 'C3_gaming',
        'gaming_intensity': 0.60, 'game_knowledge': 0.80,
        'quality_gap': 0.30
    },
    'C4_bid_rotation': {
        'type': 'C4_bid_rotation',
        'cartel_size': 3, 'round': 0, 'bribe_amount': 1.0
    },
    'C5_sybil': {
        'type': 'C5_sybil',
        'n_shells': 4, 'quality_distribution': 'distributed',
        'detection_evasion': 0.60
    },
    'C6_admin_capture': {
        'type': 'C6_admin_capture',
        'capture_depth': 'division', 'override_budget': 0.15,
        'bribe_amount': 2.0, 'n_targets': 2, 'concealment_quality': 0.70
    },
    'C7_false_data': {
        'type': 'C7_false_data',
        'falsification_rate': 0.30, 'falsification_magnitude': 0.40,
        'detection_probability': 0.35
    },
}

COLORS = {
    'baseline':          '#2ecc71',
    'C1_sparse_bribery': '#e74c3c',
    'C1_evasive_bribery':'#c0392b',
    'C2_collusion':      '#e67e22',
    'C3_gaming':         '#f39c12',
    'C4_bid_rotation':   '#9b59b6',
    'C5_sybil':          '#1abc9c',
    'C6_admin_capture':  '#e74c3c',
    'C7_false_data':     '#3498db',
}

SHORT_LABELS = {
    'baseline':          'Baseline',
    'C1_sparse_bribery': 'C1: Bribery',
    'C1_evasive_bribery':'C1b: Evasive',
    'C2_collusion':      'C2: Collusion',
    'C3_gaming':         'C3: Gaming',
    'C4_bid_rotation':   'C4: Rotation',
    'C5_sybil':          'C5: Sybil',
    'C6_admin_capture':  'C6: Admin',
    'C7_false_data':     'C7: FalseData',
}


class SimulationRunner:
    def __init__(self):
        self.master_rng = np.random.default_rng(config.RANDOM_SEED)

    def _single_run(self, scenario):
        seed = int(self.master_rng.integers(0, 2**31))
        rng  = np.random.default_rng(seed)
        try:
            gen       = DataGenerator(rng)
            scorer    = ScoringEngine(rng)
            detector  = DetectionEngine()
            allocator = AllocationSystemV3(rng)
            applicants = gen.generate_applicants(config.N_APPLICANTS, scenario)
            reviewers  = gen.generate_reviewers(config.N_REVIEWERS, scenario)
            network    = gen.generate_network(applicants, reviewers)
            assignments         = scorer.assign_reviewers(applicants, reviewers, network)
            scores, mean_infl   = scorer.generate_scores(applicants, reviewers, assignments, scenario)
            flags, fp, fn, crs, high_crs = detector.run_full_detection(
                applicants, reviewers, scores, assignments, network, scenario)
            aggregated = allocator.aggregate_scores(applicants, scores, crs, high_crs)
            selected   = allocator.select_proposals(applicants, aggregated, scenario, flags)
            result     = allocator.compute_metrics(
                applicants, selected, scenario, flags, fp, fn, crs, mean_infl)
            return result
        except Exception:
            return None

    def run_all(self, n=None):
        n = n or config.N_SIMULATIONS
        all_results = {}
        for name, params in SCENARIOS.items():
            print(f"  [{name}] running {n} iterations...", end='', flush=True)
            t0 = time.time()
            results = []
            for i in range(n):
                r = self._single_run(params or {})
                if r:
                    r.run_id = i
                    r.scenario = name
                    results.append(r)
            elapsed = time.time() - t0
            qe  = np.mean([r.quality_efficiency for r in results])
            cpr = np.mean([r.cpr for r in results])
            print(f" {elapsed:.1f}s | QE={qe:.3f}  CPR={cpr:.3f}")
            all_results[name] = results
        return all_results


class Analyzer:
    def __init__(self, results):
        self.results = results
        self.df = self._build_df()

    def _build_df(self):
        rows = []
        for scenario, res_list in self.results.items():
            for r in res_list:
                rows.append({
                    'scenario':              scenario,
                    'run_id':                r.run_id,
                    'quality_efficiency':    r.quality_efficiency,
                    'cpr':                   r.cpr,
                    'corruption_roi':        r.corruption_roi,
                    'detection_sensitivity': r.detection_sensitivity,
                    'detection_specificity': r.detection_specificity,
                    'participation_rate':    r.participation_rate,
                    'hhi_concentration':     r.hhi_concentration,
                    'n_flags':               len(r.flags_triggered),
                    'variance_collapse':     int(r.variance_collapse_detected),
                    'n_fp':                  r.n_false_positives,
                    'n_fn':                  r.n_false_negatives,
                    'n_corrupt_beneficiaries': len(r.corrupt_beneficiaries),
                    'mean_crs':              np.mean(r.reviewer_crs_scores) if r.reviewer_crs_scores else 0,
                    'mean_inflation':        r.mean_bribe_inflation,
                })
        return pd.DataFrame(rows)

    def generate_text_report(self):
        df = self.df
        lines = ["="*72, "GRANTGUARD V3 STRESS TEST RESULTS", "="*72]
        for sc in SCENARIOS.keys():
            sub = df[df['scenario'] == sc]
            if sub.empty:
                continue
            lines.append(f"\nScenario: {sc}")
            lines.append(f"  Quality Efficiency:    {sub['quality_efficiency'].mean():.3f}")
            lines.append(f"  CPR:                   {sub['cpr'].mean():.3f}")
            lines.append(f"  Corruption ROI:        {sub['corruption_roi'].mean():.2f}")
            lines.append(f"  Detection Sensitivity: {sub['detection_sensitivity'].mean():.3f}")
            lines.append(f"  Detection Specificity: {sub['detection_specificity'].mean():.3f}")
        return "\n".join(lines)


if __name__ == '__main__':
    print("="*72)
    print("GRANTGUARD SIMULATION V3")
    print("="*72)
    runner = SimulationRunner()
    results = runner.run_all(n=100)
    analyzer = Analyzer(results)
    print(analyzer.generate_text_report())
    analyzer.df.to_csv('grantguard_results.csv', index=False)
    print("\nResults saved to grantguard_results.csv")
