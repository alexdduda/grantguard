#!/usr/bin/env python3
"""
GrantGuard V5 - Cross-algorithm analysis, future scenarios C8-C15,
US/Canada jurisdiction modules, three-way comparison.
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import seaborn as sns
from scipy import stats
from scipy.special import gammaln
from scipy.stats import kendalltau, chi2
import networkx as nx
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set
from collections import defaultdict, deque
import warnings, time, sys, copy, json

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

from grantguard_simulation import (
    Config, Applicant, Reviewer, SimulationResult,
    DataGenerator, ScoringEngine, DetectionEngine,
    AllocationSystemV3, SCENARIOS as SCENARIOS_V3,
    COLORS, SHORT_LABELS, Analyzer, SimulationRunner as RunnerV3
)

OUT = '.'
SEED = 42


class ConfigV5(Config):
    K_PER_PROPOSAL          = 5
    K_HIGH_VALUE            = 7
    HIGH_VALUE_THRESHOLD    = 500_000
    CRS_PERMUTATIONS        = 400
    CRS_PERCENTILE          = 95.0
    LR_ROTATION_ALPHA       = 0.08
    LR_ROTATION_WINDOW      = 8
    LR_MIN_ROUNDS           = 4
    USE_KRUM                = True
    KRUM_F                  = 1
    RUBRIC_DIMENSIONS       = 6
    GAMING_UNIFORMITY_THRESH= 0.15
    LLM_GAMING_BOOST        = 2.8
    LLM_GAMING_FRAC         = 0.35
    AI_COLLUSION_CORR       = 0.90
    SYNTH_IDENTITY_FRAC     = 0.20
    EMERGENCY_BYPASS_PROB   = 0.60
    DEMOCRATIC_CAPTURE_BIAS = 0.30
    THRESHOLD_CREEP_RATE    = 0.05
    STATE_ACTOR_FRAC        = 0.15
    US_REVOLVING_DOOR_MONTHS= 24
    US_CONTRACT_MOD_THRESH  = 0.20
    US_MIN_BID_WINDOW_DAYS  = 30
    US_SOLE_SOURCE_ALERT    = 0.15
    CA_MIN_BID_WINDOW_DAYS  = 15
    CA_SOLE_SOURCE_ALERT    = 0.20
    CA_REGIONAL_HHI_ALERT   = 0.30
    CA_PSPC_OFFICER_THRESH  = 0.35
    N_SIMULATIONS           = 150
    RANDOM_SEED             = 42
    OUTPUT_DIR              = '.'
    BASE_VARIANCE_THRESH    = 0.50

cfg5 = ConfigV5()


class PermutationCRS:
    def __init__(self, n_permutations=None):
        self.n = n_permutations or cfg5.CRS_PERMUTATIONS

    def _compute_raw_crs(self, reviewer_id, score_pairs, all_pairs,
                          global_mean, global_std):
        vals = [sc for _, sc in score_pairs]
        rev_mean = float(np.mean(vals))
        rev_std  = float(np.std(vals)) if len(vals) > 1 else 1.5
        w1 = 0.30
        dev = abs(rev_mean - global_mean) / (global_std + 1e-6)
        score_dev = min(dev, 1.0)
        w2 = 0.35
        max_corr = 0.0
        rev_dict = dict(score_pairs)
        for other_id, other_pairs in all_pairs.items():
            if other_id == reviewer_id:
                continue
            od = dict(other_pairs)
            common = set(rev_dict) & set(od)
            if len(common) >= 2:
                s1 = [rev_dict[a] for a in common]
                s2 = [od[a] for a in common]
                if np.std(s1) > 0.01 and np.std(s2) > 0.01:
                    corr, _ = stats.pearsonr(s1, s2)
                    max_corr = max(max_corr, abs(corr))
        w3 = 0.35
        var_signal = max(0.0, 1.0 - rev_std / 1.5)
        return min(w1*score_dev + w2*max_corr + w3*var_signal, 1.0)

    def compute_thresholds(self, reviewers, scores, rng):
        rev_pairs = defaultdict(list)
        for app_id, rs in scores.items():
            for rev_id, sc in rs.items():
                rev_pairs[rev_id].append((app_id, sc))
        all_vals = [sc for pairs in rev_pairs.values() for _, sc in pairs]
        global_mean = float(np.mean(all_vals)) if all_vals else 5.0
        global_std  = float(np.std(all_vals))  if all_vals else 1.0
        thresholds = {}
        for reviewer in reviewers:
            rid = reviewer.id
            pairs = rev_pairs.get(rid, [])
            if len(pairs) < 2:
                thresholds[rid] = 0.80
                continue
            app_ids = [a for a, _ in pairs]
            sc_vals = [s for _, s in pairs]
            null_crs = []
            for _ in range(self.n):
                perm_vals = rng.permutation(sc_vals)
                perm_pairs = list(zip(app_ids, perm_vals.tolist()))
                perm_all = dict(rev_pairs)
                perm_all[rid] = perm_pairs
                null_crs.append(
                    self._compute_raw_crs(rid, perm_pairs, perm_all,
                                          global_mean, global_std))
            thresholds[rid] = float(np.percentile(null_crs, cfg5.CRS_PERCENTILE))
        return thresholds

    def compute_crs_with_thresholds(self, reviewers, scores, rng):
        rev_pairs = defaultdict(list)
        for app_id, rs in scores.items():
            for rev_id, sc in rs.items():
                rev_pairs[rev_id].append((app_id, sc))
        all_vals = [sc for pairs in rev_pairs.values() for _, sc in pairs]
        g_mean = float(np.mean(all_vals)) if all_vals else 5.0
        g_std  = float(np.std(all_vals))  if all_vals else 1.0
        crs_scores = {}
        for r in reviewers:
            pairs = rev_pairs.get(r.id, [])
            if not pairs:
                crs_scores[r.id] = 0.0
            else:
                crs_scores[r.id] = self._compute_raw_crs(
                    r.id, pairs, rev_pairs, g_mean, g_std)
        thresholds = self.compute_thresholds(reviewers, scores, rng)
        high_crs = [r.id for r in reviewers
                    if crs_scores.get(r.id, 0) > thresholds.get(r.id, 0.65)]
        return crs_scores, thresholds, high_crs


class LRRotationDetector:
    def __init__(self, window=None, alpha=None):
        self.window  = window or cfg5.LR_ROTATION_WINDOW
        self.alpha   = alpha  or cfg5.LR_ROTATION_ALPHA
        self.history = defaultdict(lambda: deque(maxlen=self.window))

    def record_round(self, all_ids, selected_ids):
        for aid in all_ids:
            self.history[aid].append(1 if aid in selected_ids else 0)

    def _log_likelihood_h0(self, seq):
        n = len(seq); k = sum(seq)
        if k == 0 or k == n:
            return -n * np.log(2)
        p = k / n
        return k*np.log(p) + (n-k)*np.log(1-p)

    def _log_likelihood_h1_cyclic(self, seqs, K):
        firm_ids = list(seqs.keys())
        if len(firm_ids) < K:
            return -np.inf
        best_ll = -np.inf
        for start_offset in range(K):
            ll = 0.0
            for i, fid in enumerate(firm_ids[:K]):
                seq = seqs[fid]
                n = len(seq)
                expected_slot = (start_offset + i) % K
                predicted = [1 if (t % K == expected_slot) else 0 for t in range(n)]
                matches = sum(a == b for a, b in zip(seq, predicted))
                p_match = max(0.01, min(0.99, matches / n))
                ll += matches * np.log(p_match) + (n-matches) * np.log(1-p_match)
            best_ll = max(best_ll, ll)
        return best_ll

    def detect_rotation(self, min_rounds=None):
        min_r = min_rounds or cfg5.LR_MIN_ROUNDS
        seqs = {fid: list(hist) for fid, hist in self.history.items()
                if len(hist) >= min_r}
        if len(seqs) < 2:
            return False, [], 1.0
        best_p = 1.0
        detected = False
        suspects = set()
        for K in [2, 3, 4]:
            if len(seqs) < K:
                continue
            ll0 = sum(self._log_likelihood_h0(s) for s in seqs.values())
            ll1 = self._log_likelihood_h1_cyclic(seqs, K)
            lr_stat = max(0.0, 2 * (ll1 - ll0))
            p_val = float(1 - chi2.cdf(lr_stat, K - 1))
            if p_val < self.alpha:
                detected = True
                best_p = min(best_p, p_val)
                for fid, seq in seqs.items():
                    if abs(np.mean(seq) - cfg5.BUDGET_FRACTION) > 0.15:
                        suspects.add(fid)
        return detected, list(suspects), best_p


def krum_aggregate(scores_dict, k, f=1):
    aggregated = {}
    for app_id, rev_scores in scores_dict.items():
        sc_list = list(rev_scores.values())
        n = len(sc_list)
        if n <= 2:
            aggregated[app_id] = float(np.median(sc_list))
            continue
        n_select = max(1, n - f - 2)
        best_score = None
        best_sum = np.inf
        for i, sc_i in enumerate(sc_list):
            diffs = sorted(abs(sc_i - sc_j)**2
                           for j, sc_j in enumerate(sc_list) if j != i)
            total = sum(diffs[:n_select])
            if total < best_sum:
                best_sum = total
                best_score = sc_i
        aggregated[app_id] = float(best_score) if best_score is not None else float(np.median(sc_list))
    return aggregated


class AIGamingDetector:
    def __init__(self, rng):
        self.rng = rng
        self.n_dims = cfg5.RUBRIC_DIMENSIONS
        self.thresh = cfg5.GAMING_UNIFORMITY_THRESH

    def score_dimensions(self, applicant, gaming_type='none'):
        base = applicant.true_quality / 10.0
        if gaming_type == 'llm':
            dim_scores = np.clip(
                base + applicant.gaming_boost/10.0
                + self.rng.normal(0, 0.03, self.n_dims), 0, 1)
        elif gaming_type == 'human':
            primary = min(1.0, base + applicant.gaming_boost/10.0 + 0.10)
            dim_scores = np.clip(
                np.array([primary if i < 3 else base + self.rng.normal(0, 0.12)
                          for i in range(self.n_dims)]), 0, 1)
        else:
            dim_scores = np.clip(base + self.rng.normal(0, 0.18, self.n_dims), 0, 1)
        return dim_scores

    def detect_llm_gaming(self, applicants, aggregated_scores):
        if not aggregated_scores:
            return [], {}
        top_quartile_thresh = np.percentile(list(aggregated_scores.values()), 75)
        flagged = []
        uniformity_scores = {}
        for a in applicants:
            gt = 'llm' if a.applicant_type == 'llm_gamed' else \
                 'human' if a.applicant_type == 'gaming' else 'none'
            dims = self.score_dimensions(a, gt)
            uniformity = float(np.std(dims))
            uniformity_scores[a.id] = uniformity
            agg = aggregated_scores.get(a.id, 0.0)
            if uniformity < self.thresh and agg >= top_quartile_thresh * 0.75:
                flagged.append(a.id)
        return flagged, uniformity_scores


FUTURE_SCENARIOS = {
    'C8_llm_gaming': {
        'type': 'C8_llm_gaming',
        'llm_boost': cfg5.LLM_GAMING_BOOST,
        'llm_frac': cfg5.LLM_GAMING_FRAC,
    },
    'C9_ai_reviewer_homogeneity': {
        'type': 'C9_ai_reviewer_homogeneity',
        'ai_correlation': cfg5.AI_COLLUSION_CORR,
        'ai_frac': 0.60,
    },
    'C10_synthetic_identity': {
        'type': 'C10_synthetic_identity',
        'synth_frac': cfg5.SYNTH_IDENTITY_FRAC,
        'quality_inflation': 0.45,
        'detection_probability': 0.25,
    },
    'C11_emergency_exploitation': {
        'type': 'C11_emergency_exploitation',
        'bypass_probability': cfg5.EMERGENCY_BYPASS_PROB,
        'override_value_frac': 0.40,
    },
    'C12_state_actor': {
        'type': 'C12_state_actor',
        'infiltration_frac': cfg5.STATE_ACTOR_FRAC,
        'bribe_amount': 1.2,
        'coordination_quality': 0.85,
    },
    'C13_threshold_creep': {
        'type': 'C13_threshold_creep',
        'creep_rate': cfg5.THRESHOLD_CREEP_RATE,
        'creep_rounds': 10,
        'target_proposals': 3,
    },
    'C14_regulatory_arbitrage': {
        'type': 'C14_regulatory_arbitrage',
        'n_sub_contracts': 8,
        'total_target_value': 500_000,
        'threshold_per_sub': 62_500,
    },
    'C15_democratic_capture': {
        'type': 'C15_democratic_capture',
        'politically_connected_frac': 0.20,
        'political_score_bias': cfg5.DEMOCRATIC_CAPTURE_BIAS,
        'reviewer_compliance_rate': 0.45,
    },
}

ALL_SCENARIOS_V5 = {**SCENARIOS_V3, **FUTURE_SCENARIOS}

FUTURE_COLORS = {
    'C8_llm_gaming':              '#ff6b6b',
    'C9_ai_reviewer_homogeneity': '#ffa07a',
    'C10_synthetic_identity':     '#20b2aa',
    'C11_emergency_exploitation': '#9370db',
    'C12_state_actor':            '#dc143c',
    'C13_threshold_creep':        '#ff8c00',
    'C14_regulatory_arbitrage':   '#4682b4',
    'C15_democratic_capture':     '#8b0000',
}
ALL_COLORS = {**COLORS, **FUTURE_COLORS}

FUTURE_SHORT = {
    'C8_llm_gaming':              'C8: LLM Gaming',
    'C9_ai_reviewer_homogeneity': 'C9: AI Homogeneity',
    'C10_synthetic_identity':     'C10: Synth Identity',
    'C11_emergency_exploitation': 'C11: Emergency',
    'C12_state_actor':            'C12: State Actor',
    'C13_threshold_creep':        'C13: Creep',
    'C14_regulatory_arbitrage':   'C14: Arbitrage',
    'C15_democratic_capture':     'C15: Dem. Capture',
}
ALL_SHORT = {**SHORT_LABELS, **FUTURE_SHORT}


class DataGeneratorV5(DataGenerator):
    def generate_applicants(self, n, scenario):
        applicants = super().generate_applicants(n, scenario)
        stype = scenario.get('type', '')
        if stype == 'C8_llm_gaming':
            boost = scenario.get('llm_boost', 2.8)
            frac  = scenario.get('llm_frac', 0.35)
            for a in applicants:
                if self.rng.random() < frac:
                    a.gaming_boost = boost
                    a.observable_features = min(10.0, a.observable_features + boost)
                    a.applicant_type = 'llm_gamed'
        elif stype == 'C10_synthetic_identity':
            frac = scenario.get('synth_frac', 0.20)
            inf  = scenario.get('quality_inflation', 0.45)
            for a in applicants:
                if self.rng.random() < frac:
                    a.observable_features = min(10.0,
                        a.observable_features + inf * (10 - a.true_quality))
                    a.applicant_type = 'synthetic'
        elif stype == 'C15_democratic_capture':
            frac = scenario.get('politically_connected_frac', 0.20)
            for a in applicants:
                if self.rng.random() < frac:
                    a.applicant_type = 'politically_connected'
        elif stype == 'C14_regulatory_arbitrage':
            n_subs = scenario.get('n_sub_contracts', 8)
            target = self.rng.integers(0, n)
            applicants[target].applicant_type = 'arbitrage_parent'
            for s in range(n_subs):
                sub = Applicant(
                    id=n + s,
                    true_quality=applicants[target].true_quality * 0.7,
                    observable_features=applicants[target].observable_features * 0.8,
                    cost=scenario.get('threshold_per_sub', 62_500),
                    applicant_type='arbitrage_sub',
                    parent_id=target
                )
                applicants.append(sub)
        return applicants

    def generate_reviewers(self, m, scenario):
        reviewers = super().generate_reviewers(m, scenario)
        stype = scenario.get('type', '')
        if stype == 'C12_state_actor':
            frac = scenario.get('infiltration_frac', 0.15)
            n_state = max(1, int(m * frac))
            for idx in self.rng.choice(m, n_state, replace=False):
                reviewers[idx].is_corrupt = True
        elif stype == 'C9_ai_reviewer_homogeneity':
            frac = scenario.get('ai_frac', 0.60)
            n_ai = int(m * frac)
            for idx in self.rng.choice(m, n_ai, replace=False):
                reviewers[idx].noise_level *= 0.25
        return reviewers


class ScoringEngineV5(ScoringEngine):
    def generate_scores(self, applicants, reviewers, assignments, scenario):
        raw_scores, mean_infl = super().generate_scores(
            applicants, reviewers, assignments, scenario)
        stype = scenario.get('type', '')
        if stype == 'C9_ai_reviewer_homogeneity':
            ai_corr = scenario.get('ai_correlation', 0.90)
            ai_revs = [r for r in reviewers if r.noise_level < 0.25]
            if len(ai_revs) >= 2:
                for app_id in raw_scores:
                    anchor_scores = {r.id: raw_scores[app_id].get(r.id)
                                     for r in ai_revs if r.id in raw_scores[app_id]}
                    if not anchor_scores:
                        continue
                    anchor_mean = np.mean(list(anchor_scores.values()))
                    for r in ai_revs:
                        if r.id in raw_scores[app_id]:
                            orig = raw_scores[app_id][r.id]
                            raw_scores[app_id][r.id] = np.clip(
                                ai_corr * anchor_mean + (1-ai_corr) * orig, 0, 10)
        elif stype == 'C13_threshold_creep':
            creep = scenario.get('creep_rate', 0.05)
            rounds = scenario.get('creep_rounds', 10)
            targets_n = scenario.get('target_proposals', 3)
            total_creep = creep * rounds
            sorted_apps = sorted(applicants, key=lambda a: a.observable_features, reverse=True)
            target_ids = {a.id for a in sorted_apps[:targets_n]}
            for app_id, rs in raw_scores.items():
                if app_id in target_ids:
                    for rev_id in rs:
                        rs[rev_id] = min(10.0, rs[rev_id] + total_creep)
        elif stype == 'C15_democratic_capture':
            bias   = scenario.get('political_score_bias', 0.30)
            comply = scenario.get('reviewer_compliance_rate', 0.45)
            for a in applicants:
                if a.applicant_type != 'politically_connected':
                    continue
                for rev_id in raw_scores.get(a.id, {}):
                    if self.rng.random() < comply:
                        raw_scores[a.id][rev_id] = min(
                            10.0, raw_scores[a.id][rev_id] + bias * 10)
        elif stype == 'C12_state_actor':
            bribe = scenario.get('bribe_amount', 1.2)
            state_revs = {r.id for r in reviewers if r.is_corrupt}
            sorted_apps = sorted(applicants, key=lambda a: a.observable_features, reverse=True)
            targets = {a.id for a in sorted_apps[:2]}
            for app_id in targets:
                for rev_id in raw_scores.get(app_id, {}):
                    if rev_id in state_revs:
                        raw_scores[app_id][rev_id] = min(
                            10.0, raw_scores[app_id][rev_id] + bribe)
        elif stype == 'C11_emergency_exploitation':
            bypass_prob = scenario.get('bypass_probability', 0.60)
            if self.rng.random() < bypass_prob:
                sorted_apps = sorted(applicants, key=lambda a: a.observable_features, reverse=True)
                beneficiary = sorted_apps[0]
                for rev_id in raw_scores.get(beneficiary.id, {}):
                    raw_scores[beneficiary.id][rev_id] = 9.5 + self.rng.normal(0, 0.1)
        return raw_scores, mean_infl


class DetectionEngineV5(DetectionEngine):
    def __init__(self, rng):
        self.rng = rng
        self.perm_crs = PermutationCRS()
        self.ai_detector = AIGamingDetector(rng)

    def run_full_detection(self, applicants, reviewers, scores, assignments,
                            network, scenario, rotation_detector=None,
                            aggregated_scores=None):
        flags = []
        stype = scenario.get('type', '')
        crs_scores, crs_thresholds, high_crs = \
            self.perm_crs.compute_crs_with_thresholds(reviewers, scores, self.rng)
        if high_crs:
            flags.append('high_crs_reviewer')
        k = len(list(scores.values())[0]) if scores else cfg5.K_PER_PROPOSAL
        threshold = cfg5.BASE_VARIANCE_THRESH * np.sqrt(max(k, 1) / cfg5.K_PER_PROPOSAL)
        susp_apps = [app_id for app_id, rs in scores.items()
                     if len(rs) >= 2 and np.std(list(rs.values())) < threshold]
        collapsed = len(susp_apps) > max(1, len(scores) * 0.15)
        if collapsed:
            flags.append('variance_collapse')
        net_pairs = self.detect_network_clusters(network, reviewers, applicants)
        if len(net_pairs) > 3:
            flags.append('network_cluster')
        if rotation_detector and rotation_detector.history:
            rot_det, rot_sus, rot_p = rotation_detector.detect_rotation()
            if rot_det:
                flags.append('lr_rotation_detected')
        if aggregated_scores:
            llm_flagged, uniformity = self.ai_detector.detect_llm_gaming(
                applicants, aggregated_scores)
            if llm_flagged:
                flags.append('ai_gaming_detected')
        if stype == 'C10_synthetic_identity':
            synth_apps = [a for a in applicants if a.applicant_type == 'synthetic']
            det_prob = scenario.get('detection_probability', 0.25)
            if synth_apps and self.rng.random() < det_prob * len(synth_apps):
                flags.append('synthetic_identity_detected')
        shell_apps = [a for a in applicants
                      if a.applicant_type in ('shell', 'arbitrage_sub')]
        if len(shell_apps) >= 2:
            flags.append('identity_splitting')
        if stype == 'C11_emergency_exploitation':
            bypass_p = scenario.get('bypass_probability', 0.60)
            if self.rng.random() < bypass_p * 0.40:
                flags.append('emergency_bypass_detected')
        if stype == 'C15_democratic_capture' and collapsed:
            flags.append('possible_political_pressure')
        if stype == 'C7_false_data':
            if self.rng_check(scenario.get('detection_probability', 0.35)):
                flags.append('false_data_detected')
        corrupt_types = {'gaming', 'llm_gamed', 'shell', 'shell_parent',
                         'colluder', 'synthetic', 'arbitrage_sub',
                         'politically_connected', 'arbitrage_parent'}
        corrupt_ids = {a.id for a in applicants if a.applicant_type in corrupt_types}
        flagged_ids = set(susp_apps)
        fp = len(flagged_ids - corrupt_ids)
        fn = len(corrupt_ids - flagged_ids)
        return flags, fp, fn, crs_scores, high_crs


class AllocationSystemV5(AllocationSystemV3):
    def aggregate_scores(self, applicants, scores, crs, high_crs):
        if cfg5.USE_KRUM:
            raw_agg = krum_aggregate(scores, k=cfg5.K_PER_PROPOSAL, f=cfg5.KRUM_F)
            for a in applicants:
                rs = scores.get(a.id, {})
                if not rs:
                    continue
                crs_weights = {r_id: max(0.1, 1.0 - crs.get(r_id, 0.0)) for r_id in rs}
                if all(w > 0.9 for w in crs_weights.values()):
                    pass
                else:
                    w_vals = [(sc, crs_weights[r_id]) for r_id, sc in rs.items()]
                    raw_agg[a.id] = float(np.average(
                        [s for s, _ in w_vals], weights=[w for _, w in w_vals]))
            return raw_agg
        else:
            return super().aggregate_scores(applicants, scores, crs, high_crs)


class SimulationRunnerV5:
    def __init__(self):
        self.master_rng = np.random.default_rng(cfg5.RANDOM_SEED)
        self.rotation_detector = LRRotationDetector()

    def _single_run(self, scenario):
        seed = int(self.master_rng.integers(0, 2**31))
        rng  = np.random.default_rng(seed)
        try:
            gen       = DataGeneratorV5(rng)
            scorer    = ScoringEngineV5(rng)
            detector  = DetectionEngineV5(rng)
            allocator = AllocationSystemV5(rng)
            applicants = gen.generate_applicants(cfg5.N_APPLICANTS, scenario)
            reviewers  = gen.generate_reviewers(cfg5.N_REVIEWERS, scenario)
            network    = gen.generate_network(applicants, reviewers)
            assignments         = scorer.assign_reviewers(applicants, reviewers, network)
            raw_scores, m_infl  = scorer.generate_scores(
                applicants, reviewers, assignments, scenario)
            pre_agg = allocator.aggregate_scores(
                applicants, raw_scores, {r.id: 0.0 for r in reviewers}, [])
            flags, fp, fn, crs, high_crs = detector.run_full_detection(
                applicants, reviewers, raw_scores, assignments, network,
                scenario, rotation_detector=self.rotation_detector,
                aggregated_scores=pre_agg)
            aggregated = allocator.aggregate_scores(applicants, raw_scores, crs, high_crs)
            selected   = allocator.select_proposals(applicants, aggregated, scenario, flags)
            self.rotation_detector.record_round([a.id for a in applicants], selected)
            result = allocator.compute_metrics(
                applicants, selected, scenario, flags, fp, fn, crs, m_infl)
            return result
        except Exception:
            return None

    def run_all(self, scenarios=None, n=None):
        scenarios = scenarios or ALL_SCENARIOS_V5
        n = n or cfg5.N_SIMULATIONS
        all_results = {}
        for name, params in scenarios.items():
            self.rotation_detector = LRRotationDetector()
            print(f"  [V5 {name}] {n} iters...", end='', flush=True)
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


class USGrantGuard:
    AGENCY_RISK_PROFILES = {
        'DoD':    {'base_risk': 0.45, 'revolving_door_rate': 0.63,
                   'single_bid_rate': 0.29, 'primary_mechanism': 'revolving_door'},
        'HHS':    {'base_risk': 0.30, 'revolving_door_rate': 0.41,
                   'single_bid_rate': 0.18, 'primary_mechanism': 'spec_gaming'},
        'DHS':    {'base_risk': 0.50, 'revolving_door_rate': 0.55,
                   'single_bid_rate': 0.35, 'primary_mechanism': 'emergency_bypass'},
        'DOE':    {'base_risk': 0.35, 'revolving_door_rate': 0.38,
                   'single_bid_rate': 0.22, 'primary_mechanism': 'state_capture'},
        'NSF/NIH':{'base_risk': 0.20, 'revolving_door_rate': 0.25,
                   'single_bid_rate': 0.08, 'primary_mechanism': 'spec_gaming'},
        'DOT':    {'base_risk': 0.40, 'revolving_door_rate': 0.35,
                   'single_bid_rate': 0.25, 'primary_mechanism': 'bid_rotation'},
    }

    def __init__(self, agency='DoD', rng=None):
        self.agency  = agency
        self.profile = self.AGENCY_RISK_PROFILES.get(agency, {})
        self.rng     = rng or np.random.default_rng(42)
        self.sam_registry      = {}
        self.fpds_contracts    = []
        self.revolving_door    = {}
        self.modification_log  = defaultdict(list)
        self.congressional_log = []
        self.audit_queue       = []
        self.sole_source_log   = []

    def register_entity(self, cage_code, entity_name, uei, naics,
                         registration_date, expiry_date):
        entity = {'cage_code': cage_code, 'name': entity_name, 'uei': uei,
                  'naics': naics, 'registered': registration_date,
                  'expires': expiry_date, 'active': True, 'flags': []}
        if registration_date > '2024-10-01':
            entity['flags'].append('RECENTLY_REGISTERED')
        self.sam_registry[uei] = entity
        return entity

    def log_official_departure(self, official_id, name, grade, agency,
                                departure_date, procurement_authority):
        self.revolving_door[official_id] = {
            'name': name, 'grade': grade, 'agency': agency,
            'departure': departure_date,
            'procurement_authority': procurement_authority,
            'post_gov_employment': []
        }

    def check_revolving_door(self, official_id, firm_uei, employment_start):
        if official_id not in self.revolving_door:
            return None
        rec = self.revolving_door[official_id]
        from datetime import datetime
        dep = datetime.strptime(rec['departure'], '%Y-%m-%d')
        emp = datetime.strptime(employment_start, '%Y-%m-%d')
        months = (emp - dep).days / 30.4
        rec['post_gov_employment'].append({'firm': firm_uei, 'start': employment_start})
        cooling_off = cfg5.US_REVOLVING_DOOR_MONTHS
        if months < cooling_off and rec['procurement_authority']:
            return (f"STOCK Act / 18 USC 207 FLAG: {rec['name']} (GS-{rec['grade']}) "
                    f"joined {firm_uei} only {months:.0f} months "
                    f"post-{rec['agency']} (cooling-off: {cooling_off} months). "
                    f"Mandatory DoJ referral if firm has active solicitations.")
        return None

    def record_contract_award(self, piid, awardee_uei, base_value, naics,
                               competition_type, n_offers):
        contract = {
            'piid': piid, 'awardee': awardee_uei, 'base_value': base_value,
            'current_value': base_value, 'naics': naics,
            'competition': competition_type, 'n_offers': n_offers,
            'modifications': [], 'flags': []
        }
        if n_offers == 1:
            contract['flags'].append('SINGLE_OFFER')
        if competition_type in ('SOLE_SOURCE', 'LIMITED'):
            contract['flags'].append('NON_COMPETITIVE')
        self.fpds_contracts.append(contract)
        return contract

    def record_modification(self, piid, mod_number, mod_value, mod_reason):
        contract = next((c for c in self.fpds_contracts if c['piid'] == piid), None)
        if not contract:
            return None
        contract['modifications'].append(
            {'number': mod_number, 'value': mod_value, 'reason': mod_reason})
        contract['current_value'] += mod_value
        total_growth = ((contract['current_value'] - contract['base_value'])
                        / contract['base_value'])
        self.modification_log[piid].append(mod_value)
        if total_growth > cfg5.US_CONTRACT_MOD_THRESH:
            alert = (f"FAR 6.302 COMPETITION BYPASS: Contract {piid} "
                     f"grown {total_growth:.1%} through "
                     f"{len(contract['modifications'])} mods. "
                     f"Re-solicitation required. IG referral recommended.")
            self.audit_queue.append({'type': 'contract_mod', 'piid': piid,
                                      'growth': total_growth, 'alert': alert})
            return alert
        return None

    def check_bid_window(self, solicitation_number, days_posted, contract_value):
        min_days = cfg5.US_MIN_BID_WINDOW_DAYS
        if contract_value > 25_000 and days_posted < min_days:
            return (f"FAR 5.203 VIOLATION: Solicitation {solicitation_number} "
                    f"posted {days_posted} days (minimum {min_days} required).")
        return None

    def flag_earmark(self, contract_id, appropriation_ref, value,
                      beneficiary_uei, congressional_sponsor):
        alert = {
            'type': 'CONGRESSIONAL_EARMARK', 'contract': contract_id,
            'appropriation': appropriation_ref, 'value': value,
            'beneficiary': beneficiary_uei, 'sponsor': congressional_sponsor,
            'risk': 'CRITICAL: bypasses FAR competition requirements entirely',
            'action': 'Mandatory post-award performance audit within 90 days'
        }
        self.congressional_log.append(alert)
        return alert

    def compute_us_cim(self, piid=None):
        cim = {}
        cim['agency_base_risk'] = self.profile.get('base_risk', 0.30)
        total = len(self.fpds_contracts)
        if total > 0:
            single_offer = sum(1 for c in self.fpds_contracts if 'SINGLE_OFFER' in c['flags'])
            cim['single_offer_rate'] = single_offer / total
            mod_contracts = sum(1 for c in self.fpds_contracts if c['modifications'])
            cim['modification_rate'] = mod_contracts / total
        else:
            cim['single_offer_rate'] = 0.0
            cim['modification_rate'] = 0.0
        recent_moves = sum(1 for rec in self.revolving_door.values()
                           if len(rec['post_gov_employment']) > 0 and rec['procurement_authority'])
        cim['revolving_door_exposure'] = min(1.0, recent_moves / max(10, total if total else 1))
        cim['congressional_earmark_rate'] = min(1.0, len(self.congressional_log) / max(10, total if total else 1))
        cim['audit_queue_density'] = min(1.0, len(self.audit_queue) / max(20, total if total else 1))
        weights = {
            'agency_base_risk': 0.20, 'single_offer_rate': 0.25,
            'modification_rate': 0.20, 'revolving_door_exposure': 0.20,
            'congressional_earmark_rate': 0.10, 'audit_queue_density': 0.05,
        }
        cim['composite_us_cim'] = sum(
            weights.get(k, 0) * v for k, v in cim.items() if k in weights)
        return cim

    def generate_us_report(self):
        cim = self.compute_us_cim()
        lines = ["="*68, f"US GRANTGUARD REPORT: {self.agency}", "="*68]
        lines.append(f"  Agency base risk:            {self.profile.get('base_risk',0):.0%}")
        lines.append(f"  Primary corruption vector:   {self.profile.get('primary_mechanism','N/A')}")
        lines.append(f"  Historical single-bid rate:  {self.profile.get('single_bid_rate',0):.0%}")
        lines.append(f"  Composite US CIM Score:      {cim.get('composite_us_cim',0):.3f}")
        lines.append(f"  Active contracts:            {len(self.fpds_contracts)}")
        lines.append(f"  Audit queue items:           {len(self.audit_queue)}")
        lines.append(f"  Congressional add-ons:       {len(self.congressional_log)}")
        lines.append("\n  Legal References:")
        lines.append("    FAR Part 5 - Publicizing Contract Actions")
        lines.append("    FAR 6.302  - Full and Open Competition Exceptions")
        lines.append("    FAR 43.103 - Contract Modification Types")
        lines.append("    STOCK Act  - Stop Trading on Congressional Knowledge")
        lines.append("    18 USC 207 - Post-employment restrictions")
        return "\n".join(lines)


class CanadaGrantGuard:
    CATEGORY_RISK_PROFILES = {
        'professional_services': {
            'sole_source_risk': 0.78, 'officer_concentration_risk': 0.71,
            'historical_sole_rate': 0.41, 'merx_window_min': 15
        },
        'it_services': {
            'sole_source_risk': 0.69, 'scope_creep_risk': 0.75,
            'historical_sole_rate': 0.38, 'merx_window_min': 20
        },
        'construction': {
            'sole_source_risk': 0.35, 'regional_concentration_risk': 0.55,
            'historical_sole_rate': 0.12, 'merx_window_min': 25
        },
        'research_grants': {
            'sole_source_risk': 0.15, 'spec_gaming_risk': 0.60,
            'historical_sole_rate': 0.06, 'merx_window_min': 30
        }
    }
    PROACTIVE_DISCLOSURE_THRESHOLDS = {
        'contracts': 10_000, 'amendments': 10_000, 'grants': 25_000,
    }

    def __init__(self, category='professional_services', rng=None):
        self.category = category
        self.profile  = self.CATEGORY_RISK_PROFILES.get(category, {})
        self.rng      = rng or np.random.default_rng(42)
        self.proactive_disclosure_log = []
        self.merx_postings    = []
        self.contract_log     = []
        self.officer_log      = defaultdict(list)
        self.regional_log     = []
        self.amendment_log    = defaultdict(list)
        self.citt_referrals   = []
        self.oag_flags        = []

    def post_to_merx(self, solicitation_number, value_estimate,
                      category, posting_days, bilingual=True):
        record = {'number': solicitation_number, 'value': value_estimate,
                  'category': category, 'days_posted': posting_days, 'bilingual': bilingual}
        self.merx_postings.append(record)
        min_days = self.profile.get('merx_window_min', 15)
        flags = []
        if posting_days < min_days:
            flags.append(f"MERX WINDOW VIOLATION: {posting_days} days "
                         f"(minimum {min_days} for {category}). CFTA Article 514 breach.")
        if not bilingual:
            flags.append("OFFICIAL LANGUAGES ACT VIOLATION: not published bilingually.")
        if value_estimate > 25_000 and posting_days < 10:
            flags.append("POSSIBLE PRE-SELECTION: < 10 day window suggests pre-briefed vendor.")
        return "; ".join(flags) if flags else None

    def disclose_contract(self, contract_number, vendor_name, value, category,
                           contracting_officer_id, competitive, award_date):
        record = {
            'contract': contract_number, 'vendor': vendor_name, 'value': value,
            'category': category, 'officer': contracting_officer_id,
            'competitive': competitive, 'date': award_date
        }
        if value >= self.PROACTIVE_DISCLOSURE_THRESHOLDS['contracts']:
            self.proactive_disclosure_log.append(record)
        self.contract_log.append(record)
        self.officer_log[contracting_officer_id].append({
            'contract': contract_number, 'competitive': competitive,
            'category': category, 'value': value
        })
        if not competitive:
            self._check_sole_source_pattern(contracting_officer_id, category)
        return record

    def _check_sole_source_pattern(self, officer_id, category):
        officer_contracts = self.officer_log.get(officer_id, [])
        cat_contracts = [c for c in officer_contracts if c['category'] == category]
        if len(cat_contracts) >= 5:
            sole_rate = sum(1 for c in cat_contracts if not c['competitive']) / len(cat_contracts)
            if sole_rate > cfg5.CA_PSPC_OFFICER_THRESH:
                self.oag_flags.append({
                    'type': 'OFFICER_SOLE_SOURCE_PATTERN', 'officer': officer_id,
                    'category': category, 'sole_source_rate': sole_rate,
                    'reference': 'PSPC Supply Manual 7.35', 'action': 'Refer to Internal Audit'
                })

    def record_award_region(self, contract_number, vendor_region, value):
        self.regional_log.append({'contract': contract_number,
                                   'region': vendor_region, 'value': value})

    def regional_favoritism_flag(self):
        if len(self.regional_log) < 10:
            return None
        total = sum(r['value'] for r in self.regional_log)
        by_region = defaultdict(float)
        for r in self.regional_log:
            by_region[r['region']] += r['value']
        hhi = sum((v/total)**2 for v in by_region.values())
        dominant = max(by_region, key=by_region.get)
        share = by_region[dominant] / total
        if hhi > cfg5.CA_REGIONAL_HHI_ALERT:
            return (f"REGIONAL CONCENTRATION FLAG: {dominant} receives "
                    f"{share:.1%} of award value (HHI={hhi:.3f}). "
                    f"PSPC procurement targets regional balance per CFTA Article 501.")
        return None

    def assess_it_phoenix_risk(self, contract_number, initial_value, amendments,
                                vendor_name, competitive_process, deliverables_defined):
        risk_score = 0.0
        factors = []
        if initial_value > 100_000_000:
            risk_score += 0.25; factors.append('large_scale_it')
        if not competitive_process:
            risk_score += 0.35; factors.append('non_competitive')
        if sum(amendments) / max(initial_value, 1) > 0.20:
            risk_score += 0.20; factors.append('amendment_creep')
        if len(amendments) > 3:
            risk_score += 0.10; factors.append('excessive_amendments')
        if not deliverables_defined:
            risk_score += 0.15; factors.append('undefined_scope')
        if not competitive_process and not deliverables_defined and len(amendments) > 2:
            risk_score += 0.10; factors.append('PHOENIX_PATTERN')
        result = {
            'contract': contract_number, 'risk_score': min(1.0, risk_score),
            'factors': factors, 'phoenix_pattern': 'PHOENIX_PATTERN' in factors,
            'total_amendment_growth': sum(amendments) / max(initial_value, 1),
            'legal_reference': 'OAG Spring 2021 Report; GCR Section 33',
            'recommended_action': (
                'Immediate OAG referral under FAA s.131' if risk_score > 0.75 else
                'Enhanced TBS monitoring' if risk_score > 0.50 else 'Standard PSPC review')
        }
        if result['phoenix_pattern']:
            self.oag_flags.append({'type': 'PHOENIX_PATTERN', 'contract': contract_number,
                                    'risk_score': result['risk_score']})
        return result

    def apply_vcg_cost_scoring(self, bids, true_cost_estimates):
        penalties = {}
        for vendor, bid in bids.items():
            estimate = true_cost_estimates.get(vendor, bid)
            overrun = (bid - estimate) / max(estimate, 1)
            penalties[vendor] = min(1.0, (overrun - 0.15) * 2) if overrun > 0.15 else 0.0
        return penalties

    def trigger_citt_referral(self, solicitation, complainant, grounds):
        referral = {
            'solicitation': solicitation, 'complainant': complainant,
            'grounds': grounds, 'status': 'FILED',
            'authority': 'CITT Act s.30.1 - Procurement Complaint',
            'timeline': '90 days to decision',
            'remedies': ['Re-solicitation', 'Contract termination',
                         'Compensation', 'Corrective measures']
        }
        self.citt_referrals.append(referral)
        return referral

    def compute_canada_cim(self):
        cim = {}
        total = max(len(self.contract_log), 1)
        sole_source = sum(1 for c in self.contract_log if not c['competitive'])
        cim['sole_source_rate'] = sole_source / total
        if self.regional_log:
            t = sum(r['value'] for r in self.regional_log)
            by_r = defaultdict(float)
            for r in self.regional_log:
                by_r[r['region']] += r['value']
            cim['regional_hhi'] = sum((v/t)**2 for v in by_r.values())
        else:
            cim['regional_hhi'] = 0.0
        officer_concs = []
        all_total = sum(c['value'] for ocs in self.officer_log.values() for c in ocs)
        for oid, contracts in self.officer_log.items():
            if all_total > 0:
                officer_concs.append(sum(c['value'] for c in contracts) / all_total)
        cim['max_officer_concentration'] = max(officer_concs) if officer_concs else 0.0
        cim['oag_flag_density'] = min(1.0, len(self.oag_flags) / max(20, total))
        cim['citt_referral_rate'] = min(1.0, len(self.citt_referrals) / max(10, total))
        weights = {
            'sole_source_rate': 0.30, 'regional_hhi': 0.20,
            'max_officer_concentration': 0.25, 'oag_flag_density': 0.15,
            'citt_referral_rate': 0.10,
        }
        cim['composite_canada_cim'] = sum(
            weights.get(k, 0) * v for k, v in cim.items() if k in weights)
        return cim

    def generate_canada_report(self):
        cim = self.compute_canada_cim()
        lines = ["="*68, f"CANADA GRANTGUARD REPORT: {self.category}", "="*68]
        lines.append(f"  Sole-source rate:           {cim['sole_source_rate']:.1%}")
        lines.append(f"  Regional HHI:               {cim['regional_hhi']:.3f}")
        lines.append(f"  Max officer concentration:  {cim['max_officer_concentration']:.3f}")
        lines.append(f"  Composite Canada CIM:       {cim['composite_canada_cim']:.3f}")
        lines.append(f"  Contracts registered:       {len(self.contract_log)}")
        lines.append(f"  OAG flags raised:           {len(self.oag_flags)}")
        lines.append(f"  CITT referrals:             {len(self.citt_referrals)}")
        lines.append("\n  Legal Framework:")
        lines.append("    GCR - Government Contracts Regulations")
        lines.append("    PSPC Supply Manual - Procurement authority")
        lines.append("    CFTA Article 501, 514 - Procurement obligations")
        lines.append("    FAA s.131 - OAG audit authority")
        lines.append("    CITT Act s.30.1 - Procurement complaint process")
        return "\n".join(lines)


if __name__ == '__main__':
    print("="*72)
    print("GRANTGUARD V5")
    print("="*72)
    rng = np.random.default_rng(SEED)
    print("\nRunning V5 core scenarios...")
    runner_v5 = SimulationRunnerV5()
    v5_core = runner_v5.run_all(scenarios=SCENARIOS_V3, n=80)
    analyzer = Analyzer(v5_core)
    print(analyzer.generate_text_report())

    print("\nUS Module Demo:")
    us = USGrantGuard(agency='DoD', rng=rng)
    us.log_official_departure(201, 'Gen. R.Hansen', 'O-10', 'OUSD(A&S)', '2023-11-01', True)
    alert = us.check_revolving_door(201, 'RAYTHEON_RTX', '2024-04-15')
    if alert: print(f"  {alert}")
    print(us.generate_us_report())

    print("\nCanada Module Demo:")
    ca = CanadaGrantGuard(category='it_services', rng=rng)
    phoenix = ca.assess_it_phoenix_risk('EN578-PHOENIX', 180_000_000,
        [18e6, 24e6, 31e6, 12e6, 19e6], 'IBM Canada', False, False)
    print(f"  Phoenix risk: {phoenix['risk_score']:.2f} - {phoenix['recommended_action']}")
    print(ca.generate_canada_report())
