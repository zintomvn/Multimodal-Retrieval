from __future__ import annotations

import re
from dataclasses import dataclass


EVENT_LABEL_RE = re.compile(
    r"(?im)(?:^|[\r\n;]|\s+)\s*"
    r"(?:"
    r"\(?\s*e\s*(?P<e_order>\d{1,2})\s*\)?"
    r"|event\s*(?P<event_order>\d{1,2})"
    r"|step\s*(?P<step_order>\d{1,2})"
    r")\s*[:.)-]\s*"
)
NUMBERED_LINE_RE = re.compile(r"(?m)^\s*(?P<order>\d{1,2})\s*[:.)-]\s+")
SOFT_SEPARATOR_RE = re.compile(
    r"\s*(?:\bthen\b|\bafter that\b|\bnext\b|\bfinally\b|"
    r"\u0111\u1ea7u\s+ti\u00ean|tr\u01b0\u1edbc\s+h\u1ebft|sau\s+\u0111\u00f3|ti\u1ebfp\s+\u0111\u1ebfn|"
    r"ti\u1ebfp\s+theo|cu\u1ed1i\s+c\u00f9ng|r\u1ed3i|;)\s*",
    flags=re.IGNORECASE,
)
CONTEXT_CHAIN_RE = re.compile(
    r"^(?P<target>.+?)\s+(?:after|sau\s+khi)\s+(?P<before>.+?)\s+"
    r"(?:and|v(?:a|à))\s+(?:before|tr(?:uoc|ước)\s+khi)\s+(?P<after>.+)$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class TemporalEventParse:
    events: list[str]
    source: str
    explicit_labels: bool = False


def parse_temporal_events(query: str, max_events: int = 8) -> TemporalEventParse:
    """Split an ordered TRAKE query into event-level search queries."""
    query = str(query or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not query:
        return TemporalEventParse(events=[], source="empty")

    context_chain = _parse_context_chain(query)
    if context_chain:
        return TemporalEventParse(events=context_chain[:max_events], source="context_relation")

    labeled = _parse_labeled_events(query, EVENT_LABEL_RE)
    if len(labeled) > 1:
        return TemporalEventParse(events=labeled[:max_events], source="event_labels", explicit_labels=True)

    numbered = _parse_labeled_events(query, NUMBERED_LINE_RE)
    if len(numbered) > 1:
        return TemporalEventParse(events=numbered[:max_events], source="numbered_lines", explicit_labels=True)

    normalized_query = " ".join(query.splitlines())
    soft_parts = [_clean_event_text(part) for part in SOFT_SEPARATOR_RE.split(normalized_query)]
    soft_parts = [part for part in soft_parts if part]
    if len(soft_parts) > 1:
        return TemporalEventParse(events=soft_parts[:max_events], source="soft_separators")

    single = _clean_event_text(labeled[0] if labeled else normalized_query)
    return TemporalEventParse(events=[single] if single else [], source="single")


def _parse_labeled_events(query: str, pattern: re.Pattern[str]) -> list[str]:
    matches = list(pattern.finditer(query))
    if not matches:
        return []
    events: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(query)
        event = _clean_event_text(query[start:end])
        if event:
            events.append(event)
    return events


def _clean_event_text(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"^\s*[-*]\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" .:-")
    return re.sub(r"^(?:l\u00e0|is|are)\s+", "", value, flags=re.IGNORECASE)


def _parse_context_chain(query: str) -> list[str]:
    """Parse KIS phrasing such as target after A and before B into A, target, B."""
    normalized = " ".join(query.splitlines())
    match = CONTEXT_CHAIN_RE.match(normalized)
    if match is None:
        return []
    values = [
        _clean_event_text(match.group("before")),
        _clean_event_text(match.group("target")),
        _clean_event_text(match.group("after")),
    ]
    return [value for value in values if value]
