"""Timestamp-aware Diagnostic Event Video-first (DEV) temporal retrieval.

This module deliberately does not share ATS/Vortex's frame-index sequence
builder.  It contains only deterministic, backend-independent operations so
the service can use the normal hybrid frame retrieval stack around it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import log
from typing import Any


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _weights(raw: dict[str, Any], keys: tuple[str, ...]) -> dict[str, float]:
    values = {key: max(0.0, float(raw.get(key, 0.0))) for key in keys}
    total = sum(values.values())
    return {key: value / total for key, value in values.items()} if total else {key: 0.0 for key in keys}


@dataclass(frozen=True)
class DevFirstCandidate:
    frame_id: str
    video_id: str
    video_code: str
    frame_idx: int
    event_index: int
    event_query: str
    timestamp_ms: int | None
    final_retrieval_score: float
    semantic_raw_score: float | None = None
    semantic_relative_score: float = 0.0
    text_raw_score: float | None = None
    text_relative_score: float = 0.0
    rrf_score: float = 0.0
    calibrated_event_score: float = 0.0
    diagnostic_score: float = 0.0


@dataclass(frozen=True)
class DevFirstSequence:
    video_id: str
    video_code: str
    candidates: list[DevFirstCandidate]
    score: float
    details: dict[str, Any] = field(default_factory=dict)


def calibrate_candidates(candidates: list[DevFirstCandidate], config: dict[str, Any]) -> list[DevFirstCandidate]:
    """Combine ranking and hybrid evidence without max-score normalization."""
    weights = _weights(config, ("relative_weight", "absolute_weight", "hybrid_weight"))
    calibrated: list[DevFirstCandidate] = []
    total = max(1, len(candidates))
    for rank, candidate in enumerate(sorted(candidates, key=lambda item: item.final_retrieval_score, reverse=True), start=1):
        # Log rank is stable even when an event's absolute candidate pool is weak.
        relative = _clamp((1.0 / (1.0 + log(1.0 + rank))) / (1.0 / (1.0 + log(2.0))))
        hybrid = _clamp(candidate.final_retrieval_score)
        absolute_available = candidate.semantic_raw_score is not None
        absolute = _clamp(float(candidate.semantic_raw_score or 0.0))
        active = dict(weights)
        if not absolute_available:
            active["absolute_weight"] = 0.0
            active = _weights(active, tuple(active))
        score = active["relative_weight"] * relative + active["absolute_weight"] * absolute + active["hybrid_weight"] * hybrid
        calibrated.append(
            DevFirstCandidate(
                **{**candidate.__dict__, "semantic_relative_score": relative, "calibrated_event_score": _clamp(score)}
            )
        )
    return calibrated


def temporal_nms(candidates: list[DevFirstCandidate], window_ms: int, limit_per_group: int | None = None) -> list[DevFirstCandidate]:
    """Suppress only same-video, same-event near duplicates; timestamps win."""
    grouped: dict[tuple[str, int], list[DevFirstCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault((candidate.video_id, candidate.event_index), []).append(candidate)
    selected: list[DevFirstCandidate] = []
    for group in grouped.values():
        kept: list[DevFirstCandidate] = []
        for candidate in sorted(group, key=lambda item: (item.calibrated_event_score, item.final_retrieval_score), reverse=True):
            is_duplicate = any(
                abs((candidate.timestamp_ms if candidate.timestamp_ms is not None else candidate.frame_idx) -
                    (prior.timestamp_ms if prior.timestamp_ms is not None else prior.frame_idx)) < window_ms
                for prior in kept
            )
            if not is_duplicate:
                kept.append(candidate)
            if limit_per_group and len(kept) >= limit_per_group:
                break
        selected.extend(kept)
    return sorted(selected, key=lambda item: (item.video_code, item.event_index, item.timestamp_ms if item.timestamp_ms is not None else item.frame_idx))


def event_probe(candidates: list[DevFirstCandidate], top_k: int) -> dict[str, float]:
    sample = sorted(candidates, key=lambda item: item.calibrated_event_score, reverse=True)[: max(1, top_k)]
    if not sample:
        return {"selectivity": 0.0, "margin": 0.0, "top_confidence": 0.0, "score": 0.0}
    unique_videos = len({item.video_id for item in sample})
    top = sample[0].calibrated_event_score
    mean_tail = sum(item.calibrated_event_score for item in sample[1: min(6, len(sample))]) / max(1, min(5, len(sample) - 1))
    return {
        "selectivity": _clamp(1 - unique_videos / max(1, len(sample))),
        "margin": _clamp(top - mean_tail),
        "top_confidence": top,
        "score": 0.0,
    }


def select_diagnostic_event(event_plans: list[dict[str, Any]], probes: list[dict[str, float]], config: dict[str, Any]) -> tuple[int, list[dict[str, float]]]:
    probe_cfg = config.get("diagnostic_probe", {}) if isinstance(config.get("diagnostic_probe"), dict) else {}
    planner_weight = max(0.0, float(probe_cfg.get("planner_weight", 0.55)))
    probe_weight = max(0.0, float(probe_cfg.get("probe_weight", 0.45)))
    total = planner_weight + probe_weight or 1.0
    planner_weight, probe_weight = planner_weight / total, probe_weight / total
    diagnostics: list[dict[str, float]] = []
    for index, plan in enumerate(event_plans):
        probe = probes[index] if index < len(probes) else {}
        prior = _clamp(float(plan.get("diagnostic_prior", plan.get("importance", 0.5))))
        probe_score = _clamp(0.4 * float(probe.get("selectivity", 0.0)) + 0.3 * float(probe.get("margin", 0.0)) + 0.3 * float(probe.get("top_confidence", 0.0)))
        diagnostics.append({"event_index": float(index + 1), "planner_prior": prior, "probe_score": probe_score, "diagnostic_score": planner_weight * prior + probe_weight * probe_score})
    best = max(range(len(diagnostics)), key=lambda index: diagnostics[index]["diagnostic_score"]) if diagnostics else 0
    return best + 1, diagnostics


def score_candidate_videos(candidates: list[DevFirstCandidate], config: dict[str, Any]) -> list[tuple[str, float]]:
    cfg = config.get("video_scoring", {}) if isinstance(config.get("video_scoring"), dict) else {}
    weights = _weights(cfg, ("best_weight", "top_m_mean_weight", "view_agreement_weight"))
    top_m = max(1, int(cfg.get("top_m", 3)))
    groups: dict[str, list[DevFirstCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.video_id, []).append(candidate)
    scored: list[tuple[str, float]] = []
    for video_id, group in groups.items():
        ranked = sorted(group, key=lambda item: item.calibrated_event_score, reverse=True)
        best = ranked[0].calibrated_event_score
        mean = sum(item.calibrated_event_score for item in ranked[:top_m]) / min(top_m, len(ranked))
        # The shared retrieval result currently does not expose a stable view-id
        # per frame. Treat agreement as unavailable rather than rewarding a long
        # or repetitive shot by its number of candidates.
        agreement = 0.0
        scored.append((video_id, _clamp(weights["best_weight"] * best + weights["top_m_mean_weight"] * mean + weights["view_agreement_weight"] * agreement)))
    return sorted(scored, key=lambda item: item[1], reverse=True)


def resolve_edge_constraints(event_count: int, edges: list[dict[str, Any]], config: dict[str, Any]) -> dict[tuple[int, int], int | None]:
    classes = config.get("temporal_gap_classes", {}) if isinstance(config.get("temporal_gap_classes"), dict) else {}
    resolved: dict[tuple[int, int], int | None] = {}
    for edge in edges:
        try:
            source, target = int(edge.get("from_event")), int(edge.get("to_event"))
        except (TypeError, ValueError):
            continue
        if not (1 <= source < target <= event_count):
            continue
        gap = classes.get(str(edge.get("gap_class", "unknown")), {})
        resolved[(source, target)] = gap.get("max_gap_ms") if isinstance(gap, dict) else None
    return resolved


def _valid_extension(previous: DevFirstCandidate | None, current: DevFirstCandidate, constraints: dict[tuple[int, int], int | None]) -> bool:
    if previous is None:
        return True
    previous_coordinate = previous.timestamp_ms if previous.timestamp_ms is not None else previous.frame_idx
    current_coordinate = current.timestamp_ms if current.timestamp_ms is not None else current.frame_idx
    if current_coordinate <= previous_coordinate:
        return False
    maximum = constraints.get((previous.event_index, current.event_index))
    return maximum is None or current_coordinate - previous_coordinate <= maximum


def score_dev_first_sequence(candidates: list[DevFirstCandidate], event_weights: list[float], diagnostic_index: int, config: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    weights = [max(0.0, value) for value in event_weights] or [1.0]
    total_weight = sum(weights) or float(len(weights))
    matched = {candidate.event_index: candidate for candidate in candidates}
    evidence = sum(weights[index - 1] * candidate.calibrated_event_score for index, candidate in matched.items() if index <= len(weights)) / total_weight
    coverage = sum(weights[index - 1] for index in matched if index <= len(weights)) / total_weight
    strong_threshold = float(config.get("strong_match_threshold", 0.55))
    strong_coverage = sum(weights[index - 1] for index, candidate in matched.items() if index <= len(weights) and candidate.calibrated_event_score >= strong_threshold) / total_weight
    diagnostic = matched.get(diagnostic_index).calibrated_event_score if diagnostic_index in matched else 0.0
    gaps = []
    ordered = sorted(candidates, key=lambda item: item.event_index)
    for left, right in zip(ordered, ordered[1:]):
        left_time = left.timestamp_ms if left.timestamp_ms is not None else left.frame_idx
        right_time = right.timestamp_ms if right.timestamp_ms is not None else right.frame_idx
        gaps.append(max(0, right_time - left_time))
    gap_penalty = min(1.0, (sum(gaps) / len(gaps) / max(1, 300000)) if gaps else 0.0)
    scoring = _weights(config.get("sequence_scoring", {}) if isinstance(config.get("sequence_scoring"), dict) else {}, ("evidence_weight", "coverage_weight", "strong_coverage_weight", "diagnostic_weight", "missing_penalty_weight", "temporal_gap_penalty_weight"))
    # The coefficients are normalized once for a bounded, comparable score.
    score = (scoring["evidence_weight"] * evidence + scoring["coverage_weight"] * coverage + scoring["strong_coverage_weight"] * strong_coverage + scoring["diagnostic_weight"] * diagnostic - scoring["missing_penalty_weight"] * (1 - coverage) - scoring["temporal_gap_penalty_weight"] * gap_penalty)
    return _clamp(score), {"weighted_event_evidence": evidence, "coverage": coverage, "strong_coverage": strong_coverage, "missing_ratio": 1 - coverage, "diagnostic_match": diagnostic, "temporal_gap_penalty": gap_penalty}


def build_dev_first_sequences(candidate_sets: list[list[DevFirstCandidate]], event_weights: list[float], diagnostic_index: int, edges: list[dict[str, Any]], config: dict[str, Any], min_match: int, limit: int) -> list[DevFirstSequence]:
    constraints = resolve_edge_constraints(len(candidate_sets), edges, config)
    by_video: dict[str, list[list[DevFirstCandidate]]] = {}
    for event_index, candidates in enumerate(candidate_sets, start=1):
        for candidate in candidates:
            by_video.setdefault(candidate.video_id, [[] for _ in candidate_sets])[event_index - 1].append(candidate)
    beam_width = max(1, int(config.get("sequence_beam_width", 200)))
    sequences: list[DevFirstSequence] = []
    for video_id, per_event in by_video.items():
        states: list[list[DevFirstCandidate]] = [[]]
        for candidates in per_event:
            next_states: list[list[DevFirstCandidate]] = []
            for state in states:
                next_states.append(state)  # explicit skip: missing event remains in denominator.
                previous = state[-1] if state else None
                for candidate in candidates:
                    if _valid_extension(previous, candidate, constraints):
                        next_states.append([*state, candidate])
            next_states.sort(key=lambda state: score_dev_first_sequence(state, event_weights, diagnostic_index, config)[0], reverse=True)
            states = next_states[:beam_width]
        code = next((item.video_code for event in per_event for item in event), video_id)
        for state in states:
            if len(state) < min_match:
                continue
            score, details = score_dev_first_sequence(state, event_weights, diagnostic_index, config)
            sequences.append(DevFirstSequence(video_id=video_id, video_code=code, candidates=state, score=score, details=details))
    return diversify_sequences(sequences, config)[:limit]


def diversify_sequences(sequences: list[DevFirstSequence], config: dict[str, Any]) -> list[DevFirstSequence]:
    window = max(0, int(config.get("sequence_nms_window_ms", 3000)))
    maximum = max(1, int(config.get("max_sequences_per_video", 3)))
    selected: list[DevFirstSequence] = []
    per_video: dict[str, list[DevFirstSequence]] = {}
    for sequence in sorted(sequences, key=lambda item: item.score, reverse=True):
        prior = per_video.setdefault(sequence.video_id, [])
        timestamps = [candidate.timestamp_ms if candidate.timestamp_ms is not None else candidate.frame_idx for candidate in sequence.candidates]
        duplicate = any(
            len(timestamps) == len(other.candidates) and all(abs(current - (candidate.timestamp_ms if candidate.timestamp_ms is not None else candidate.frame_idx)) <= window for current, candidate in zip(timestamps, other.candidates))
            for other in prior
        )
        if not duplicate and len(prior) < maximum:
            prior.append(sequence)
            selected.append(sequence)
    return selected
