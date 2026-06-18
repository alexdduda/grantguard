#!/usr/bin/env python3
"""
GrantGuard V4 - Iterative Improvements + Step 5: US/Canada Adaptations
Fix 1: Two-layer rubric (public 60% + confidential 40%)
Fix 2: Cross-round Mann-Kendall rotation detection
Fix 3: Adaptive variance threshold
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import seaborn as sns
from scipy import stats
from scipy.stats import kendalltau
import networkx as nx
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from collections import defaultdict, deque
import warnings
import time
import copy
import sys
sys.path.insert(0, '.')
from grantguard_simulation import (
    Config, Applicant, Reviewer, SimulationResult,
    DataGenerator, ScoringEngine, DetectionEngine,
    AllocationSystemV3, SCENARIOS, COLORS, SHORT_LABELS,
    Analyzer, SimulationRunner as RunnerV3
)

warnings.filterwarnings('ignore')


class ConfigV4(Config):
    PUBLIC_RUBRIC_WEIGHT    = 0.60
    CONFIDENTIAL_WEIGHT     = 0.40
    CONFIDENTIAL_POOL_SIZE  = 12
    CONFIDENTIAL_DRAWN      = 4
    ROTATION_HISTORY_ROUNDS = 6
    ROTATION_KENDALL_ALPHA  = 0.10
    BASE_VARIANCE_THRESH    = 0.50
    K_REFERENCE             = 3
    TARGET_SPECIFICITY      = 0.85
    REVOLVING_DOOR_MONTHS   = 24
    SOLE_SOURCE_ALERT_RATE  = 0.20
    CONTRACT_MOD_ALERT_RATE = 0.20
    REGIONAL_HHI_THRESHOLD  = 0.30

config4 = ConfigV4()


class TwoLayerRubric:
    def __init__(self, rng):
        self.rng = rng
        self.pool = self.rng.normal(1.0, 0.25, config4.CONFIDENTIAL_POOL_SIZE)
        self.active_indices = self.rng.choice(
            config4.CONFIDENTIAL_POOL_SIZE,
            config4.CONFIDENTIAL_DRAWN, replace=False)

    def split_score(self, raw_score, gaming_boost):
        public = raw_score
        confidential = raw_score - gaming_boost
        adjusted = (config4.PUBLIC_RUBRIC_WEIGHT * public
                    + config4.CONFIDENTIAL_WEIGHT * confidential)
        return public, adjusted

    def apply_to_scores(self, scores, applicants):
        app_map = {a.id: a for a in applicants}
        adjusted = {}
        for app_id, rev_scores in scores.items():
            a = app_map.get(app_id)
            boost = a.gaming_boost if a else 0.0
            adjusted[app_id] = {}
            for rev_id, sc in rev_scores.items():
                _, adj = self.split_score(sc, boost)
                adjusted[app_id][rev_id] = np.clip(adj, 0.0, 10.0)
        return adjusted


class BidRotationDetector:
    def __init__(self, window=None):
        self.window = window or config4.ROTATION_HISTORY_ROUNDS
        self.history = defaultdict(lambda: deque(maxlen=self.window))

    def record_round(self, all_applicant_ids, selected_ids):
        for aid in all_applicant_ids:
            self.history[aid].append(1 if aid in selected_ids else 0)

    def detect_rotation(self):
        seqs = {fid: list(hist) for fid, hist in self.history.items()
                if len(hist) >= self.window}
        if len(seqs) < 2:
            return False, [], 1.0
        min_p = 1.0
        detected = False
        suspects = set()
        for fid1, fid2 in [pair for pair in
                            [(list(seqs.keys())[i], list(seqs.keys())[j])
                             for i in range(len(seqs))
                             for j in range(i+1, len(seqs))]]:
            s1 = seqs[fid1]
            s2 = seqs[fid2]
            if len(s1) < 3 or len(s2) < 3:
                continue
            tau, p = kendalltau(s1, s2)
            if tau < -0.5 and p < config4.ROTATION_KENDALL_ALPHA:
                detected = True
                min_p = min(min_p, p)
                suspects.add(fid1)
                suspects.add(fid2)
        return detected, list(suspects), min_p


class AdaptiveDetectionEngine(DetectionEngine):
    def adaptive_variance_threshold(self, k):
        return config4.BASE_VARIANCE_THRESH * np.sqrt(k / config4.K_REFERENCE)

    def detect_variance_collapse(self, scores, k=None):
        k = k or config4.K_PER_PROPOSAL
        threshold = self.adaptive_variance_threshold(k)
        suspicious = []
        for app_id, rs in scores.items():
            if len(rs) >= 2 and np.std(list(rs.values())) < threshold:
                suspicious.append(app_id)
        collapsed = len(suspicious) > max(1, len(scores) * 0.15)
        return collapsed, suspicious

    def run_full_detection(self, applicants, reviewers, scores, assignments,
                            network, scenario, rotation_detector=None, k=None):
        k = k or config4.K_PER_PROPOSAL
        flags = []
        stype = scenario.get('type', '')
        collapsed, susp_apps = self.detect_variance_collapse(scores, k=k)
        if collapsed:
            flags.append('variance_collapse')
        crs = self.compute_crs(reviewers, scores)
        high_crs = [r_id for r_id, sc in crs.items()
                    if sc > config4.CRS_THRESHOLD]
        if high_crs:
            flags.append('high_crs_reviewer')
        net_pairs = self.detect_network_clusters(network, reviewers, applicants)
        if len(net_pairs) > 3:
            flags.append('network_cluster')
        if stype == 'C5_sybil':
            shells = [a for a in applicants if a.applicant_type == 'shell']
            if len(shells) >= 2:
                flags.append('identity_splitting')
        if rotation_detector and len(list(rotation_detector.history.values())) > 0:
            rot_detected, rot_suspects, rot_p = rotation_detector.detect_rotation()
            if rot_detected:
                flags.append('bid_rotation_detected')
        elif stype == 'C4_bid_rotation':
            flags.append('bid_pattern_anomaly')
        if stype == 'C7_false_data':
            if self.rng_check(scenario.get('detection_probability', 0.35)):
                flags.append('false_data_detected')
        corrupt_ids = set(self._corrupt_beneficiary_ids(applicants, scenario))
        flagged_ids = set(susp_apps)
        fp = len(flagged_ids - corrupt_ids)
        fn = len(corrupt_ids - flagged_ids)
        return flags, fp, fn, crs, high_crs


class SimulationRunnerV4:
    def __init__(self):
        self.master_rng = np.random.default_rng(config4.RANDOM_SEED)
        self.rotation_detector = BidRotationDetector()

    def _single_run(self, scenario):
        seed = int(self.master_rng.integers(0, 2**31))
        rng  = np.random.default_rng(seed)
        try:
            gen       = DataGenerator(rng)
            scorer    = ScoringEngine(rng)
            detector  = AdaptiveDetectionEngine()
            allocator = AllocationSystemV3(rng)
            rubric    = TwoLayerRubric(rng)
            applicants = gen.generate_applicants(config4.N_APPLICANTS, scenario)
            reviewers  = gen.generate_reviewers(config4.N_REVIEWERS, scenario)
            network    = gen.generate_network(applicants, reviewers)
            assignments          = scorer.assign_reviewers(applicants, reviewers, network)
            scores, mean_infl    = scorer.generate_scores(
                applicants, reviewers, assignments, scenario)
            adjusted_scores = rubric.apply_to_scores(scores, applicants)
            flags, fp, fn, crs, high_crs = detector.run_full_detection(
                applicants, reviewers, adjusted_scores, assignments, network,
                scenario, rotation_detector=self.rotation_detector,
                k=config4.K_PER_PROPOSAL)
            aggregated = allocator.aggregate_scores(
                applicants, adjusted_scores, crs, high_crs)
            selected   = allocator.select_proposals(
                applicants, aggregated, scenario, flags)
            self.rotation_detector.record_round(
                [a.id for a in applicants], selected)
            result = allocator.compute_metrics(
                applicants, selected, scenario, flags, fp, fn, crs, mean_infl)
            return result
        except Exception:
            return None

    def run_all(self, n=None):
        n = n or config4.N_SIMULATIONS
        all_results = {}
        for name, params in SCENARIOS.items():
            self.rotation_detector = BidRotationDetector()
            print(f"  [V4 {name}] {n} iters...", end='', flush=True)
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


class USProcurementModule:
    SOLE_SOURCE_JUSTIFICATION_RISK = {
        'FAR6.302-1': 0.20,
        'FAR6.302-2': 0.85,
        'FAR6.302-3': 0.70,
        'FAR6.302-4': 0.30,
        'FAR6.302-5': 0.55,
        'FAR6.302-6': 0.45,
        'FAR6.302-7': 0.65,
    }
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
        self.sam_registry     = {}
        self.fpds_contracts   = []
        self.revolving_door   = {}
        self.modification_log = defaultdict(list)
        self.congressional_log = []
        self.audit_queue      = []

    def register_entity(self, cage_code, entity_name, uei, naics,
                         registration_date, expiry_date):
        entity = {
            'cage_code': cage_code, 'name': entity_name, 'uei': uei,
            'naics': naics, 'registered': registration_date,
            'expires': expiry_date, 'active': True, 'flags': []
        }
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
        if months < config4.REVOLVING_DOOR_MONTHS and rec['procurement_authority']:
            return (f"STOCK Act / 18 USC 207 FLAG: {rec['name']} joined {firm_uei} "
                    f"only {months:.0f} months post-{rec['agency']} "
                    f"(cooling-off: {config4.REVOLVING_DOOR_MONTHS} months). "
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
        if total_growth > config4.CONTRACT_MOD_ALERT_RATE:
            alert = (f"FAR 6.302 COMPETITION BYPASS: Contract {piid} "
                     f"grown {total_growth:.1%} through "
                     f"{len(contract['modifications'])} mods. "
                     f"FAR 43.103(b) re-solicitation required. "
                     f"Referral to agency IG recommended.")
            self.audit_queue.append({'type': 'contract_mod', 'piid': piid,
                                      'growth': total_growth, 'alert': alert})
            return alert
        return None

    def check_bid_window(self, solicitation_number, days_posted, contract_value):
        if contract_value > 25_000 and days_posted < config4.US_MIN_BID_WINDOW_DAYS:
            return (f"FAR 5.203 VIOLATION: Solicitation {solicitation_number} "
                    f"posted {days_posted} days "
                    f"(minimum {config4.US_MIN_BID_WINDOW_DAYS} required for >${25_000:,}).")
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
            single_offer = sum(1 for c in self.fpds_contracts
                               if 'SINGLE_OFFER' in c['flags'])
            cim['single_offer_rate'] = single_offer / total
            mod_contracts = sum(1 for c in self.fpds_contracts if c['modifications'])
            cim['modification_rate'] = mod_contracts / total
        recent_moves = sum(1 for rec in self.revolving_door.values()
                           if len(rec['post_gov_employment']) > 0
                           and rec['procurement_authority'])
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
        lines.append(f"  Agency base risk:       {self.profile.get('base_risk',0):.0%}")
        lines.append(f"  Primary mechanism:      {self.profile.get('primary_mechanism','N/A')}")
        lines.append(f"  Composite US CIM:       {cim.get('composite_us_cim',0):.3f}")
        lines.append(f"  Contracts:              {len(self.fpds_contracts)}")
        lines.append(f"  Audit queue:            {len(self.audit_queue)}")
        lines.append(f"  Congressional add-ons:  {len(self.congressional_log)}")
        return "\n".join(lines)


class CanadaProcurementModule:
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

    def __init__(self, category='professional_services', rng=None):
        self.category = category
        self.profile  = self.CATEGORY_RISK_PROFILES.get(category, {})
        self.rng      = rng or np.random.default_rng(42)
        self.proactive_disclosure_log = []
        self.merx_postings   = []
        self.contract_log    = []
        self.officer_log     = defaultdict(list)
        self.regional_log    = []
        self.amendment_log   = defaultdict(list)
        self.citt_referrals  = []
        self.oag_flags       = []

    def post_to_merx(self, solicitation_number, value_estimate,
                      category, posting_days, bilingual=True):
        record = {'number': solicitation_number, 'value': value_estimate,
                  'category': category, 'days_posted': posting_days,
                  'bilingual': bilingual}
        self.merx_postings.append(record)
        min_days = self.profile.get('merx_window_min', 15)
        flags = []
        if posting_days < min_days:
            flags.append(f"MERX WINDOW VIOLATION: {posting_days} days "
                         f"(minimum {min_days} for {category}). CFTA Article 514 breach.")
        if not bilingual:
            flags.append("OFFICIAL LANGUAGES ACT VIOLATION: not bilingual.")
        return "; ".join(flags) if flags else None

    def disclose_contract(self, contract_number, vendor_name, value,
                           category, contracting_officer_id, competitive, award_date):
        record = {
            'contract': contract_number, 'vendor': vendor_name, 'value': value,
            'category': category, 'officer': contracting_officer_id,
            'competitive': competitive, 'date': award_date
        }
        if value >= 10_000:
            self.proactive_disclosure_log.append(record)
        self.contract_log.append(record)
        self.officer_log[contracting_officer_id].append({
            'contract': contract_number, 'competitive': competitive,
            'category': category, 'value': value
        })
        return record

    def record_award_region(self, contract_number, vendor_region, value):
        self.regional_log.append({'contract': contract_number,
                                   'region': vendor_region, 'value': value})

    def regional_favoritism_flag(self):
        if not self.regional_log:
            return None
        total = sum(r['value'] for r in self.regional_log)
        by_r = {}
        for r in self.regional_log:
            by_r[r['region']] = by_r.get(r['region'], 0) + r['value']
        hhi = sum((v/total)**2 for v in by_r.values())
        dominant = max(by_r, key=by_r.get)
        share = by_r[dominant] / total
        if hhi > config4.REGIONAL_HHI_THRESHOLD:
            return (f"REGIONAL CONCENTRATION: {dominant} receives {share:.1%} "
                    f"(HHI={hhi:.3f}). CFTA Article 501 review required.")
        return None

    def assess_it_phoenix_risk(self, contract_number, initial_value,
                                amendments, vendor_name, competitive_process,
                                deliverables_defined):
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
        return {
            'contract': contract_number, 'risk_score': min(1.0, risk_score),
            'factors': factors, 'phoenix_pattern': 'PHOENIX_PATTERN' in factors,
            'total_amendment_growth': sum(amendments) / max(initial_value, 1),
            'recommended_action': (
                'Immediate OAG referral' if risk_score > 0.75 else
                'Enhanced TBS monitoring' if risk_score > 0.50 else 'Standard review')
        }

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
            'authority': 'CITT Act s.30.1',
            'timeline': '90 days to decision',
            'remedies': ['Re-solicitation', 'Contract termination',
                         'Compensation', 'Corrective measures']
        }
        self.citt_referrals.append(referral)
        return referral

    def compute_canada_cim(self):
        cim = {}
        total = max(len(self.contract_log), 1)
        sole = sum(1 for c in self.contract_log if not c['competitive'])
        cim['sole_source_rate'] = sole / total
        if self.regional_log:
            t = sum(r['value'] for r in self.regional_log)
            by_r = {}
            for r in self.regional_log:
                by_r[r['region']] = by_r.get(r['region'], 0) + r['value']
            cim['regional_hhi'] = sum((v/t)**2 for v in by_r.values())
        else:
            cim['regional_hhi'] = 0.0
        all_total = sum(c['value'] for ocs in self.officer_log.values() for c in ocs)
        officer_concs = []
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
        lines.append(f"  Sole-source rate:       {cim['sole_source_rate']:.1%}")
        lines.append(f"  Regional HHI:           {cim['regional_hhi']:.3f}")
        lines.append(f"  Officer concentration:  {cim['max_officer_concentration']:.3f}")
        lines.append(f"  Composite Canada CIM:   {cim['composite_canada_cim']:.3f}")
        lines.append(f"  OAG flags:              {len(self.oag_flags)}")
        lines.append(f"  CITT referrals:         {len(self.citt_referrals)}")
        return "\n".join(lines)


if __name__ == '__main__':
    print("="*72)
    print("GRANTGUARD V4")
    print("="*72)
    print("\nRunning V4 simulations...")
    runner = SimulationRunnerV4()
    results = runner.run_all(n=100)
    analyzer = Analyzer(results)
    print(analyzer.generate_text_report())

    print("\nUS Module Demo:")
    us = USProcurementModule(agency='DoD')
    us.log_official_departure(1, 'J. Smith', '15', 'DoD', '2024-01-15', True)
    alert = us.check_revolving_door(1, 'RAYTHEON', '2024-07-01')
    if alert:
        print(f"  {alert}")
    print(us.generate_us_report())

    print("\nCanada Module Demo:")
    ca = CanadaProcurementModule(category='it_services')
    phoenix = ca.assess_it_phoenix_risk('IT-001', 180_000_000,
        [18e6, 24e6, 31e6], 'IBM Canada', False, False)
    print(f"  Phoenix risk: {phoenix['risk_score']:.2f} | {phoenix['recommended_action']}")
    print(ca.generate_canada_report())
