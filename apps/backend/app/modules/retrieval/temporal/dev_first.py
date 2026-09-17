"""Timestamp-aware Diagnostic Event Video-first (DEV) temporal retrieval.

DEV diagnoses a small, diverse set of videos first, then runs a second
retrieval inside that set.  It uses Vortex's lightweight same-video temporal
re-scoring, while keeping the planner's order and time-window constraints.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    # A contextual frame sampled from a known temporal gap.  It is useful for
    # inspection when AutoShot has no representative frame in that interval,
    # but must never masquerade as semantic evidence for an event.
    is_dynamic_sample: bool = False


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
        calibrated.append(replace(
            candidate,
            semantic_relative_score=relative,
            calibrated_event_score=_clamp(score),
        ))
    return calibrated


def temporal_nms(candidates: list[DevFirstCandidate], window_ms: int, limit_per_group: int | None = None) -> list[DevFirstCandidate]:
    """Suppress only same-video, same-event near duplicates; timestamps win."""
    grouped: dict[tuple[str, int], list[DevFirstCandidate]] = {}
    # Group for each video
    for candidate in candidates:
        grouped.setdefault((candidate.video_id, candidate.event_index), []).append(candidate)
    selected: list[DevFirstCandidate] = []
    for group in grouped.values():
        kept: list[DevFirstCandidate] = []
        for candidate in sorted(group, key=lambda item: (item.calibrated_event_score, item.final_retrieval_score), reverse=True):
            is_duplicate = any(
                # A temporal NMS window is expressed in milliseconds.  With no
                # timestamp there is no safe frame-rate conversion, so only an
                # exact same frame is a duplicate; the per-video cap still
                # bounds work and prevents a noisy fallback pool.
                abs(_coordinate(candidate) - _coordinate(prior)) < (
                    window_ms if candidate.timestamp_ms is not None and prior.timestamp_ms is not None else 1
                )
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
    top = sample[0].calibrated_event_score # top 1 - best score

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


def score_candidate_videos_across_events(
    candidate_sets: list[list[DevFirstCandidate]],
    diagnostic_index: int,
    config: dict[str, Any],
    event_weights: list[float] | None = None,
) -> list[tuple[str, float]]:
    """Rank videos from early evidence across the entire event chain.

    A diagnostic event narrows the corpus, but it can be visually generic (for
    example, "ingredients in a pot").  Scoring only that event can discard the
    target before later, more discriminative events are searched.  This keeps
    the diagnostic event dominant while rewarding independent support and
    coverage from the remaining event probes.
    """
    cfg = config.get("video_scoring", {}) if isinstance(config.get("video_scoring"), dict) else {}
    weights = _weights(
        {
            "diagnostic": cfg.get("diagnostic_weight", 0.55),
            "cross_event": cfg.get("cross_event_weight", 0.35),
            "coverage": cfg.get("coverage_weight", 0.10),
        },
        ("diagnostic", "cross_event", "coverage"),
    )
    event_count = max(1, len(candidate_sets))
    normalized_event_weights = [max(0.0, float(value)) for value in (event_weights or [])]
    if len(normalized_event_weights) != event_count or sum(normalized_event_weights) <= 0:
        normalized_event_weights = [1.0] * event_count
    total_event_weight = sum(normalized_event_weights)
    per_video: dict[str, dict[int, float]] = {}
    for event_index, candidates in enumerate(candidate_sets, start=1):
        for candidate in candidates:
            scores = per_video.setdefault(candidate.video_id, {})
            scores[event_index] = max(scores.get(event_index, 0.0), candidate.calibrated_event_score)

    ranked: list[tuple[str, float]] = []
    for video_id, event_scores in per_video.items():
        diagnostic = event_scores.get(diagnostic_index, 0.0)
        cross_event = sum(
            normalized_event_weights[event_index - 1] * score
            for event_index, score in event_scores.items()
        ) / total_event_weight
        coverage = sum(
            normalized_event_weights[event_index - 1]
            for event_index in event_scores
        ) / total_event_weight
        score = (
            weights["diagnostic"] * diagnostic
            + weights["cross_event"] * cross_event
            + weights["coverage"] * coverage
        )
        ranked.append((video_id, _clamp(score)))
    return sorted(ranked, key=lambda item: item[1], reverse=True)


def select_diverse_candidate_videos(
    candidate_sets: list[list[DevFirstCandidate]],
    ranked_videos: list[tuple[str, float]],
    event_weights: list[float],
    limit: int,
    config: dict[str, Any],
) -> list[str]:
    """Select a small, event-diverse video set before the local Milvus pass.

    A global top-k list is commonly dominated by one visually repetitive video.
    The first pass reserves candidates for every LLM-planned event, in
    importance order, then fills the remaining slots by the aggregate DEV
    score.  A video is included only once, so the expensive local retrieval is
    spread across genuinely different videos rather than near-duplicate frames.
    """
    limit = max(1, limit)
    weights = [max(0.0, float(value)) for value in event_weights]
    if len(weights) != len(candidate_sets) or sum(weights) <= 0:
        weights = [1.0] * len(candidate_sets)
    per_event_quota = max(1, int(config.get("diverse_videos_per_event", 2)))
    selected: list[str] = []
    selected_set: set[str] = set()

    for event_index in sorted(range(len(candidate_sets)), key=lambda index: (-weights[index], index)):
        videos = sorted(
            candidate_sets[event_index],
            key=lambda item: (item.calibrated_event_score, item.final_retrieval_score),
            reverse=True,
        )
        added = 0
        for candidate in videos:
            if candidate.video_id in selected_set:
                continue
            selected.append(candidate.video_id)
            selected_set.add(candidate.video_id)
            added += 1
            if len(selected) >= limit or added >= per_event_quota:
                break
        if len(selected) >= limit:
            return selected

    for video_id, _score in ranked_videos:
        if video_id in selected_set:
            continue
        selected.append(video_id)
        selected_set.add(video_id)
        if len(selected) >= limit:
            break
    return selected


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


def _valid_extension(
    previous: DevFirstCandidate | None,
    current: DevFirstCandidate,
    constraints: dict[tuple[int, int], int | None],
    default_max_gap_ms: int | None = None,
) -> bool:
    if previous is None:
        return True
    # Planner gaps and request delta are milliseconds.  A dataset without
    # timestamps must retain strict frame order but must not compare a frame
    # index against a millisecond threshold (which creates a hidden FPS
    # assumption and can reject valid sequences).
    if previous.timestamp_ms is None or current.timestamp_ms is None:
        return current.frame_idx > previous.frame_idx
    previous_coordinate = previous.timestamp_ms if previous.timestamp_ms is not None else previous.frame_idx
    current_coordinate = current.timestamp_ms if current.timestamp_ms is not None else current.frame_idx
    if current_coordinate <= previous_coordinate:
        return False
    maximum = constraints.get((previous.event_index, current.event_index), default_max_gap_ms)
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
    timestamp_coordinate = all(candidate.timestamp_ms is not None for candidate in candidates)
    gaps = []
    ordered = sorted(candidates, key=lambda item: item.event_index)
    if timestamp_coordinate:
        for left, right in zip(ordered, ordered[1:]):
            gaps.append(max(0, int(right.timestamp_ms) - int(left.timestamp_ms)))
    # Use the DEV profile's expected local transition scale rather than a
    # fixed five-minute denominator.  ``short`` transitions can be adjacent
    # AutoShot frames; rewarding compact chains prevents generic events from
    # being stitched across unrelated distant shots.
    gap_reference_ms = max(1, int(config.get("temporal_gap_reference_ms", 300000)))
    gap_penalty = min(1.0, (sum(gaps) / len(gaps) / gap_reference_ms) if gaps else 0.0)
    scoring = _weights(config.get("sequence_scoring", {}) if isinstance(config.get("sequence_scoring"), dict) else {}, ("evidence_weight", "coverage_weight", "strong_coverage_weight", "diagnostic_weight", "missing_penalty_weight", "temporal_gap_penalty_weight"))
    # The coefficients are normalized once for a bounded, comparable score.
    score = (scoring["evidence_weight"] * evidence + scoring["coverage_weight"] * coverage + scoring["strong_coverage_weight"] * strong_coverage + scoring["diagnostic_weight"] * diagnostic - scoring["missing_penalty_weight"] * (1 - coverage) - scoring["temporal_gap_penalty_weight"] * gap_penalty)
    return _clamp(score), {"weighted_event_evidence": evidence, "coverage": coverage, "strong_coverage": strong_coverage, "missing_ratio": 1 - coverage, "diagnostic_match": diagnostic, "temporal_gap_penalty": gap_penalty, "temporal_gap_coordinate": "timestamp_ms" if timestamp_coordinate else "frame_idx_unpenalized"}


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


def build_dev_first_vortex_ats_sequences(
    candidate_sets: list[list[DevFirstCandidate]],
    event_weights: list[float],
    diagnostic_index: int,
    edges: list[dict[str, Any]],
    config: dict[str, Any],
    min_match: int,
    limit: int,
    default_max_gap_ms: int,
) -> list[DevFirstSequence]:
    """Run Vortex re-scoring over DEV's already-local candidate pools.

    The historical public name is retained for callers, but DEV no longer calls
    ATS or uses Vortex as a candidate-video filter.  Each diagnostic anchor is
    expanded independently to the best compatible event on either side.  The
    expansion is bounded by planner edges (or the request delta), preserving
    both event order and a concrete temporal search window.
    """
    if not candidate_sets:
        return []

    constraints = resolve_edge_constraints(len(candidate_sets), edges, config)
    default_gap = max(1, int(default_max_gap_ms))
    grouped: dict[str, list[list[DevFirstCandidate]]] = {}
    for event_position, event_candidates in enumerate(candidate_sets):
        for candidate in event_candidates:
            grouped.setdefault(candidate.video_id, [[] for _ in candidate_sets])[event_position].append(candidate)

    diagnostic_position = min(max(diagnostic_index - 1, 0), len(candidate_sets) - 1)
    sequences: list[DevFirstSequence] = []
    for video_id, per_event in grouped.items():
        # No Vortex/ATS truncation occurs here: the local retriever controls the
        # small pool, and every diagnostic frame is eligible as an anchor.  A
        # globally selected video can legitimately miss that one event after
        # AutoShot/local retrieval while retaining a distinctive terminal or
        # intermediate event.  In that case, use its most important available
        # event as a fallback anchor rather than dropping the video before the
        # temporal scorer can apply the missing-event penalty.
        anchor_position = diagnostic_position
        anchor_source = "diagnostic"
        anchors = per_event[anchor_position]
        if not anchors:
            available_positions = [
                position for position, event_candidates in enumerate(per_event)
                if event_candidates
            ]
            if not available_positions:
                continue
            anchor_position = max(
                available_positions,
                key=lambda position: (
                    event_weights[position] if position < len(event_weights) else 1.0,
                    max(item.calibrated_event_score for item in per_event[position]),
                    -position,
                ),
            )
            anchors = per_event[anchor_position]
            anchor_source = "event_fallback"
        for anchor in anchors:
            before: list[DevFirstCandidate] = []
            boundary = anchor
            for position in range(anchor_position - 1, -1, -1):
                eligible = [
                    item for item in per_event[position]
                    if _valid_extension(item, boundary, constraints, default_gap)
                ]
                if eligible:
                    selected = max(eligible, key=lambda item: (item.calibrated_event_score, _coordinate(item)))
                    before.append(selected)
                    boundary = selected

            after: list[DevFirstCandidate] = []
            boundary = anchor
            for position in range(anchor_position + 1, len(per_event)):
                eligible = [
                    item for item in per_event[position]
                    if _valid_extension(boundary, item, constraints, default_gap)
                ]
                if eligible:
                    selected = max(eligible, key=lambda item: (item.calibrated_event_score, -_coordinate(item)))
                    after.append(selected)
                    boundary = selected

            candidates = [*reversed(before), anchor, *after]
            if len(candidates) < min_match:
                continue
            score, details = score_dev_first_sequence(candidates, event_weights, diagnostic_index, config)
            vortex_score = sum(
                (event_weights[item.event_index - 1] if item.event_index <= len(event_weights) else 1.0)
                * item.calibrated_event_score
                for item in candidates
            )
            code = next((item.video_code for event in per_event for item in event), video_id)
            sequences.append(DevFirstSequence(
                video_id=video_id,
                video_code=code,
                candidates=candidates,
                score=score,
                details={
                    **details,
                    "sequence_constructor": "dev_local_vortex_temporal_rescore",
                    "vortex_score": vortex_score,
                    "anchor_event_index": anchor_position + 1,
                    "anchor_source": anchor_source,
                    "temporal_window_default_ms": default_gap,
                    "planner_edge_count": len(constraints),
                },
            ))
    return diversify_sequences(sequences, config)[:limit]


def _coordinate(candidate: DevFirstCandidate) -> int:
    return candidate.timestamp_ms if candidate.timestamp_ms is not None else candidate.frame_idx


def diversify_sequences(sequences: list[DevFirstSequence], config: dict[str, Any]) -> list[DevFirstSequence]:
    window = max(0, int(config.get("sequence_nms_window_ms", 3000)))
    maximum = max(1, int(config.get("max_sequences_per_video", 3)))
    selected: list[DevFirstSequence] = []
    per_video: dict[str, list[DevFirstSequence]] = {}
    for sequence in sorted(sequences, key=lambda item: item.score, reverse=True):
        prior = per_video.setdefault(sequence.video_id, [])
        timestamps = [_coordinate(candidate) for candidate in sequence.candidates]
        duplicate = any(
            len(timestamps) == len(other.candidates) and all(
                abs(current - _coordinate(candidate)) <= (
                    window if sequence.candidates[position].timestamp_ms is not None and candidate.timestamp_ms is not None else 0
                )
                for position, (current, candidate) in enumerate(zip(timestamps, other.candidates))
            )
            for other in prior
        )
        if not duplicate and len(prior) < maximum:
            prior.append(sequence)
            selected.append(sequence)
    return selected
