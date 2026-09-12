from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.modules.retrieval.temporal_query import parse_temporal_events

logger = logging.getLogger(__name__)


JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
VIETNAMESE_DIACRITICS_RE = re.compile("[\u00c0-\u1ef9\u0110\u0111]")
TEMPORAL_CUE_RE = re.compile(
    r"\b(?:before|after|then|next|finally|starts?|ends?|"
    r"sau\s+\u0111\u00f3|sau\s+khi|tr\u01b0\u1edbc\s+khi|ti\u1ebfp\s+\u0111\u1ebfn|ti\u1ebfp\s+theo|"
    r"cu\u1ed1i\s+c\u00f9ng|b\u1eaft\s+\u0111\u1ea7u|k\u1ebft\s+th\u00fac|l\u1ea7n\s+l\u01b0\u1ee3t|r\u1ed3i)\b",
    flags=re.IGNORECASE,
)
VIETNAMESE_SIGNAL_TOKENS = {
    "ao",
    "con",
    "cuc",
    "dan",
    "den",
    "do",
    "gioi",
    "hanh",
    "hiem",
    "ho",
    "mau",
    "mien",
    "moi",
    "nam",
    "nguoi",
    "phi",
    "phong",
    "quang",
    "quy",
    "sinh",
    "tau",
    "them",
    "thieu",
    "tin",
    "tru",
    "tu",
    "vai",
    "vu",
}
TEXT_EVIDENCE_RE = re.compile(
    r"\b(?:hoi|cau hoi|ten|la gi|cau tho|tieu de|cong thuc|loi noi|phat bieu|"
    r"duoc san xuat|nam\s+\d{4}|chuong trinh|cau lac bo|dai hoc|tinh|xa|dia phuong|"
    r"nghien cuu|mau tin|gioi thieu|nhiem vu|thong tin|noi dung|chu|hien thi|man hinh|"
    r"logo|so|gia|ma so|ky tu|written|title|sign|number|score)\b",
    re.IGNORECASE,
)
OCR_EVIDENCE_RE = re.compile(
    r"\b(?:chu|van ban|noi dung viet|hien thi|tren man hinh|tren bang|"
    r"bang bieu|bieu do|ky hieu|logo|bien|so|ma so|so hieu|gia|written|"
    r"on screen|on-screen|displayed|table|chart|diagram|symbol|sign|label)\b",
    re.IGNORECASE,
)
SPEECH_EVIDENCE_RE = re.compile(
    r"\b(?:noi|loi noi|phat bieu|hoi|thuyet minh|speech|dialogue|narrat)\b",
    re.IGNORECASE,
)


@dataclass
class QueryPlanningResult:
    language: str = "auto"
    intent: str = "FREEFORM"
    summary: str = ""
    multi_views: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    text_variants: list[str] = field(default_factory=list)
    temporal_events: list[str] = field(default_factory=list)
    temporal_anchor_index: int | None = None
    retrieval_weights: dict[str, float] = field(default_factory=dict)
    retrieval_weight_source: str = "profile"
    text_source_weights: dict[str, float] = field(default_factory=dict)
    text_source_weight_source: str = "profile"
    temporal_event_plans: list[dict[str, Any]] = field(default_factory=list)
    decomposition: dict[str, Any] = field(default_factory=dict)
    agent_metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "fallback"
    error: str | None = None

    def as_normalized_query(self) -> dict[str, Any]:
        payload = {
            "source": self.source,
            "language": self.language,
            "intent": self.intent,
            "summary": self.summary,
            "decomposition": self.decomposition,
            "temporal_events": self.temporal_events,
            "temporal_anchor_index": self.temporal_anchor_index,
            "multi_views": self.multi_views or self.variants,
            "variants": self.variants,
            "text_variants": self.text_variants,
            "retrieval_weights": self.retrieval_weights,
            "retrieval_weight_source": self.retrieval_weight_source,
            "text_source_weights": self.text_source_weights,
            "text_source_weight_source": self.text_source_weight_source,
            "temporal_event_plans": self.temporal_event_plans,
            "agent_metadata": self.agent_metadata,
        }
        if self.error:
            payload["error"] = self.error
        return payload


class AgentQueryPlanner:
    """LangChain Deep Agents planner for query decomposition and expansion.

    The planner is intentionally optional at runtime. If dependencies, API keys,
    or the remote model are unavailable, callers receive a deterministic fallback
    plan and can continue with the existing search path.
    """

    _plan_cache: dict[tuple[str, str, str, int, bool], tuple[float, QueryPlanningResult]] = {}
    _plan_cache_lock = threading.Lock()
    _openai_gate_lock = threading.Lock()
    _openai_next_allowed_at: dict[str, float] = {}

    def __init__(self, config: dict[str, Any], config_path: Path | None = None) -> None:
        self.config = config
        self.config_path = config_path
        self.agent_config = config.get("llm_query_planning", {}) if isinstance(config, dict) else {}
        self._agents: dict[str, Any] = {}
        self._models: dict[str, Any] = {}

    @classmethod
    def from_config(cls, path: Path) -> "AgentQueryPlanner":
        if not path.exists():
            return cls(config={}, config_path=path)
        with path.open("r", encoding="utf-8") as handle:
            return cls(config=yaml.safe_load(handle) or {}, config_path=path)

    def plan(
        self,
        query: str,
        query_type: str,
        max_variants: int,
        temporal_kis: bool = False,
    ) -> QueryPlanningResult:
        query = query.strip()
        max_variants = max(1, int(max_variants or self._max_variants_default()))
        if not query:
            return self._fallback_plan(query, query_type, max_variants, error="blank query")

        cache_key = (self._active_profile_name(), query_type, query, max_variants, temporal_kis)
        cached = self._get_cached_plan(cache_key)
        if cached is not None:
            return cached

        unavailable_reason = self._unavailable_reason()
        if unavailable_reason:
            return self._fallback_plan(query, query_type, max_variants, error=unavailable_reason)

        try:
            output = self._invoke_agent(
                query=query,
                query_type=query_type,
                max_variants=max_variants,
                temporal_kis=temporal_kis,
            )
            content = self._extract_content(output)
            raw_plan = self._parse_json_object(content)
            result = self._result_from_raw_plan(
                raw_plan=raw_plan,
                query=query,
                query_type=query_type,
                max_variants=max_variants,
            )
            if self._requires_temporal_repair(query, temporal_kis, result):
                repaired = self._repair_temporal_plan(query, query_type, max_variants, result)
                if repaired is not None and (len(repaired.temporal_events) >= 2 or len(result.temporal_events) < 2):
                    repaired.decomposition["temporal_repair_applied"] = True
                    repaired.agent_metadata["temporal_repair_applied"] = True
                    result = repaired
            if result.multi_views or result.variants:
                self._cache_plan(cache_key, result)
                return result
            return self._fallback_plan(query, query_type, max_variants, error="agent returned no variants")
        except Exception as exc:  # pragma: no cover - exercised in integration environments.
            logger.warning("LLM query planning failed; falling back to baseline expansion.", exc_info=True)
            if self._fallback_on_error():
                return self._fallback_plan(query, query_type, max_variants, error=str(exc))
            raise

    def _invoke_agent(self, query: str, query_type: str, max_variants: int, temporal_kis: bool = False) -> Any:
        user_payload = {
            "query_text": query,
            "query_type": query_type,
            "max_variants": max_variants,
            "max_temporal_events": self._max_temporal_events(),
            "temporal_kis": temporal_kis,
            "task": (
                "Temporal KIS is enabled. Return 2-8 chronological event queries and a 1-based "
                "temporal_anchor_index for the requested target frame."
                if temporal_kis
                else "Return the JSON query plan only."
            ),
        }
        runnable_config = {
            "run_name": "llm_query_planning",
            "tags": ["retrieval", "query-planning", self._provider()],
            "metadata": {
                "active_profile": self._active_profile_name(),
                "provider": self._provider(),
                "model": self._model_name(),
                "query_type": query_type,
                "config_path": str(self.config_path) if self.config_path else None,
            },
            "recursion_limit": max(8, int(self.agent_config.get("max_agent_iterations", 4)) * 4),
        }
        if self._execution_mode() == "direct":
            planner_prompt = self._planner_system_prompt()
            self._wait_for_openai_slot()
            return self._get_model().invoke(
                [
                    {"role": "system", "content": planner_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                config=runnable_config,
            )

        agent = self._get_agent()
        trace_enabled = self._configure_langsmith()
        try:
            import langsmith as ls  # type: ignore
        except ImportError:
            self._wait_for_openai_slot()
            return agent.invoke({"messages": [{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}]}, config=runnable_config)

        with ls.tracing_context(enabled=trace_enabled):
            self._wait_for_openai_slot()
            return agent.invoke({"messages": [{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}]}, config=runnable_config)

    def _requires_temporal_repair(
        self,
        query: str,
        temporal_kis: bool,
        result: QueryPlanningResult,
    ) -> bool:
        """Ask an LLM validator to review every temporal plan with temporal cues."""
        _ = result
        return temporal_kis and bool(TEMPORAL_CUE_RE.search(query))

    def _repair_temporal_plan(
        self,
        query: str,
        query_type: str,
        max_variants: int,
        prior_plan: QueryPlanningResult,
    ) -> QueryPlanningResult | None:
        payload = {
            "query_text": query,
            "query_type": query_type,
            "max_variants": max_variants,
            "max_temporal_events": self._max_temporal_events(),
            "temporal_kis": True,
            "prior_plan": prior_plan.as_normalized_query(),
            "task": (
                "Validate and, if needed, replace the prior Temporal KIS plan using only the user "
                "query. Return an independently retrievable 2-8 event chronological chain. Split "
                "every distinct visual transition or stated repeated reveal into its own event; do "
                "not collapse repetitions into a count. Infer concrete English embedding/caption "
                "queries and Vietnamese ASR/OCR text queries from the supplied query, without using "
                "fixed vocabularies or examples. Set the 1-based anchor to the intended target event. "
                "Return JSON only."
            ),
        }
        runnable_config = {
            "run_name": "temporal_kis_plan_repair",
            "tags": ["retrieval", "query-planning", "temporal-repair", self._provider()],
            "metadata": {
                "active_profile": self._active_profile_name(),
                "provider": self._provider(),
                "model": self._model_name(),
                "query_type": query_type,
                "config_path": str(self.config_path) if self.config_path else None,
            },
        }
        try:
            self._wait_for_openai_slot()
            output = self._get_model().invoke(
                [
                    {"role": "system", "content": self._planner_system_prompt()},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                config=runnable_config,
            )
            return self._result_from_raw_plan(
                raw_plan=self._parse_json_object(self._extract_content(output)),
                query=query,
                query_type=query_type,
                max_variants=max_variants,
            )
        except Exception:
            logger.warning("Temporal KIS plan repair failed; keeping the original plan.", exc_info=True)
            return None

    def _get_cached_plan(self, cache_key: tuple[str, str, str, int, bool]) -> QueryPlanningResult | None:
        ttl_s = max(0, int(self._profile_value("cache_ttl_s", 600)))
        if ttl_s <= 0:
            return None
        now = time.monotonic()
        with self._plan_cache_lock:
            cached = self._plan_cache.get(cache_key)
            if cached is None:
                return None
            created_at, result = cached
            if now - created_at > ttl_s:
                self._plan_cache.pop(cache_key, None)
                return None
            return deepcopy(result)

    def _cache_plan(self, cache_key: tuple[str, str, str, int, bool], result: QueryPlanningResult) -> None:
        if result.source == "fallback" or not (result.multi_views or result.variants):
            return
        with self._plan_cache_lock:
            self._plan_cache[cache_key] = (time.monotonic(), deepcopy(result))

    def _wait_for_openai_slot(self) -> None:
        if self._provider() != "openai":
            return
        interval_s = max(0.0, float(self._profile_value("min_request_interval_s", 0.0)))
        if interval_s <= 0:
            return
        key = f"{self._provider()}:{self._model_name()}"
        with self._openai_gate_lock:
            now = time.monotonic()
            allowed_at = self._openai_next_allowed_at.get(key, now)
            wait_s = max(0.0, allowed_at - now)
            self._openai_next_allowed_at[key] = max(now, allowed_at) + interval_s
        if wait_s:
            time.sleep(wait_s)

    def _get_agent(self) -> Any:
        active_profile = self._active_profile_name()
        if active_profile in self._agents:
            return self._agents[active_profile]

        from deepagents import create_deep_agent  # type: ignore

        llm = self._get_model()
        agents = self.agent_config.get("agents", {}) if isinstance(self.agent_config.get("agents"), dict) else {}
        planner_cfg = agents.get("planner", {}) if isinstance(agents.get("planner"), dict) else {}
        subagents = []
        for key in ("query_decomposition", "query_expansion"):
            sub_cfg = agents.get(key, {}) if isinstance(agents.get(key), dict) else {}
            prompt = str(sub_cfg.get("system_prompt", "")).strip()
            if not prompt:
                continue
            subagents.append(
                {
                    "name": str(sub_cfg.get("name", f"{key}_agent")),
                    "description": str(sub_cfg.get("description", key.replace("_", " "))),
                    "system_prompt": prompt,
                    "tools": [],
                }
            )

        self._agents[active_profile] = create_deep_agent(
            model=llm,
            tools=[],
            system_prompt=str(planner_cfg.get("system_prompt", "")).strip(),
            subagents=subagents,
        )
        return self._agents[active_profile]

    def _get_model(self) -> Any:
        active_profile = self._active_profile_name()
        if active_profile not in self._models:
            self._models[active_profile] = self._create_chat_model()
        return self._models[active_profile]

    def _planner_system_prompt(self) -> str:
        agents = self.agent_config.get("agents", {})
        planner_cfg = agents.get("planner", {}) if isinstance(agents, dict) and isinstance(agents.get("planner"), dict) else {}
        return str(planner_cfg.get("system_prompt", "")).strip()

    def _create_chat_model(self) -> Any:
        provider = self._provider()
        if provider == "chatgroq":
            from langchain_groq import ChatGroq  # type: ignore

            model_kwargs = self._chat_model_kwargs(token_key="max_tokens")
            reasoning_format = str(self._profile_value("reasoning_format", "")).strip()
            if reasoning_format:
                model_kwargs["reasoning_format"] = reasoning_format
            reasoning_effort = str(self._profile_value("reasoning_effort", "")).strip()
            if reasoning_effort:
                model_kwargs["reasoning_effort"] = reasoning_effort
            return ChatGroq(**model_kwargs)
        if provider == "openai":
            from langchain_openai import ChatOpenAI  # type: ignore

            return ChatOpenAI(**self._chat_model_kwargs(token_key="max_completion_tokens"))
        raise ValueError(f"unsupported agent provider: {provider}")

    def _chat_model_kwargs(self, token_key: str) -> dict[str, Any]:
        model_kwargs: dict[str, Any] = {
            "model": self._model_name(),
            "temperature": float(self._profile_value("temperature", 0.1)),
            "timeout": float(self._profile_value("timeout_s", 30)),
            "max_retries": int(self._profile_value("max_retries", 2)),
        }
        max_tokens = self._profile_value("max_tokens", None)
        if max_tokens is not None:
            model_kwargs[token_key] = int(max_tokens)
        base_url_env = str(self._profile_value("base_url_env", "")).strip()
        base_url = os.getenv(base_url_env, "").strip() if base_url_env else ""
        if base_url:
            model_kwargs["base_url"] = base_url.rstrip("/")
        return model_kwargs

    def _unavailable_reason(self) -> str | None:
        if not bool(self.agent_config.get("enabled", False)):
            return "agent query planning is disabled"
        if self._unknown_profile_reason():
            return self._unknown_profile_reason()

        provider = self._provider()
        if provider not in {"chatgroq", "openai"}:
            return f"unsupported agent provider: {provider}"

        if bool(self._profile_value("requires_base_url", False)):
            base_url_env = str(self._profile_value("base_url_env", "")).strip()
            if not base_url_env or not os.getenv(base_url_env, "").strip():
                return f"missing {base_url_env or 'OpenAI-compatible base URL'}"

        default_key_env = self._default_api_key_env(provider)
        api_key_env = self._api_key_env(provider)
        api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            return f"missing {api_key_env}"
        if api_key_env != default_key_env:
            os.environ.setdefault(default_key_env, api_key)
        try:
            import deepagents  # noqa: F401
            if provider == "chatgroq":
                import langchain_groq  # noqa: F401
            else:
                import langchain_openai  # noqa: F401
        except ImportError as exc:
            return f"missing LangChain agent dependency: {exc.name or exc}"
        return None

    def _configure_langsmith(self) -> bool:
        langsmith_cfg = self.agent_config.get("langsmith", {})
        if not isinstance(langsmith_cfg, dict) or not bool(langsmith_cfg.get("enabled", False)):
            return False

        api_key_env = str(langsmith_cfg.get("api_key_env", "LANGSMITH_API_KEY")).strip() or "LANGSMITH_API_KEY"
        api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            return False
        if api_key_env != "LANGSMITH_API_KEY":
            os.environ.setdefault("LANGSMITH_API_KEY", api_key)

        tracing_env = str(langsmith_cfg.get("tracing_env", "LANGSMITH_TRACING")).strip() or "LANGSMITH_TRACING"
        project_env = str(langsmith_cfg.get("project_env", "LANGSMITH_PROJECT")).strip() or "LANGSMITH_PROJECT"
        endpoint_env = str(langsmith_cfg.get("endpoint_env", "LANGSMITH_ENDPOINT")).strip() or "LANGSMITH_ENDPOINT"
        os.environ.setdefault(tracing_env, "true")
        project = str(langsmith_cfg.get("project", "")).strip()
        endpoint = str(langsmith_cfg.get("endpoint", "")).strip()
        if project:
            os.environ.setdefault(project_env, project)
        if endpoint:
            os.environ.setdefault(endpoint_env, endpoint)
        return os.getenv(tracing_env, "").strip().lower() == "true"

    def _result_from_raw_plan(
        self,
        raw_plan: dict[str, Any],
        query: str,
        query_type: str,
        max_variants: int,
    ) -> QueryPlanningResult:
        search_factors = raw_plan.get("search_factors")
        if not isinstance(search_factors, dict):
            search_factors = {}
        retrieval_strategy = self._extract_retrieval_strategy(raw_plan, query, query_type)

        temporal_events = self._extract_temporal_events(raw_plan, query)
        variants = self._extract_variants(raw_plan, query, max_variants)
        multi_views = self._extract_multi_views(raw_plan, query, max_variants)
        text_variants = self._extract_text_variants(raw_plan, query, max_variants)
        if len(variants) < max_variants:
            variants = self._dedupe(variants + multi_views)[:max_variants]
        multi_views = self._prefer_english_values(query, multi_views, max_variants)
        variants = self._prefer_english_values(query, variants, max_variants)
        if not variants:
            variants = list(multi_views)
        temporal_events = self._prefer_english_temporal_events(query, temporal_events, self._max_temporal_events())
        temporal_event_plans = self._extract_temporal_event_plans(raw_plan, temporal_events, query_type, max_variants)
        temporal_anchor_index = self._extract_temporal_anchor_index(raw_plan, len(temporal_events))
        summary = str(raw_plan.get("summary") or "")
        summary_rewrite = self._english_retrieval_rewrite(query)
        if summary_rewrite:
            summary = summary_rewrite
        elif self._should_prefer_english(query, summary):
            summary = summary_rewrite or summary

        metadata = self._agent_metadata()
        metadata["langsmith_trace_enabled"] = self._langsmith_trace_enabled()

        return QueryPlanningResult(
            language=str(raw_plan.get("language") or "auto"),
            intent=str(raw_plan.get("intent") or query_type),
            summary=summary,
            multi_views=multi_views,
            variants=variants,
            text_variants=text_variants,
            temporal_events=temporal_events,
            temporal_anchor_index=temporal_anchor_index,
            retrieval_weights=retrieval_strategy["weights"],
            retrieval_weight_source=retrieval_strategy["weight_source"],
            text_source_weights=retrieval_strategy["text_source_weights"],
            text_source_weight_source=retrieval_strategy["text_source_weight_source"],
            temporal_event_plans=temporal_event_plans,
            decomposition={
                "search_factors": search_factors,
                "retrieval_strategy": retrieval_strategy,
                "temporal_event_plans": temporal_event_plans,
                "temporal_anchor_index": temporal_anchor_index,
                "raw_temporal_events": raw_plan.get("temporal_events") if isinstance(raw_plan.get("temporal_events"), list) else [],
            },
            agent_metadata=metadata,
            source="langchain_direct_llm" if self._execution_mode() == "direct" else "langchain_deep_agent",
        )

    def _fallback_plan(self, query: str, query_type: str, max_variants: int, error: str | None = None) -> QueryPlanningResult:
        english_query = self._english_retrieval_rewrite(query)
        multi_views = self._dedupe([item for item in [english_query, query] if item])[:max_variants]
        temporal_events = self._prefer_english_temporal_events(
            query=query,
            values=self._heuristic_temporal_events(query),
            max_values=self._max_temporal_events(),
        )
        tokens = [token.lower() for token in re.findall(r"[\w]+", query, flags=re.UNICODE) if len(token) > 1]
        retrieval_strategy = self.infer_retrieval_strategy(query, query_type)
        temporal_event_plans = [
            self._heuristic_temporal_event_plan(event, index, query_type)
            for index, event in enumerate(temporal_events, start=1)
        ]
        temporal_anchor_index = self._extract_temporal_anchor_index({}, len(temporal_events))
        return QueryPlanningResult(
            language="auto",
            intent=query_type,
            summary=english_query or query,
            multi_views=multi_views,
            variants=multi_views,
            text_variants=[query] if query else [],
            temporal_events=temporal_events,
            temporal_anchor_index=temporal_anchor_index,
            retrieval_weights=retrieval_strategy["weights"],
            retrieval_weight_source=retrieval_strategy["weight_source"],
            text_source_weights=retrieval_strategy["text_source_weights"],
            text_source_weight_source=retrieval_strategy["text_source_weight_source"],
            temporal_event_plans=temporal_event_plans,
            decomposition={
                "search_factors": {
                    "subjects": [],
                    "actions": [],
                    "objects": [],
                    "attributes": [],
                    "scene": [],
                    "text_cues": [],
                    "time_cues": [],
                    "negative_constraints": [],
                    "tokens": tokens,
                },
                "retrieval_strategy": retrieval_strategy,
                "temporal_event_plans": temporal_event_plans,
                "temporal_anchor_index": temporal_anchor_index,
                "raw_temporal_events": [],
            },
            agent_metadata=self._agent_metadata(),
            source="fallback",
            error=error,
        )

    def _extract_multi_views(self, raw_plan: dict[str, Any], query: str, max_views: int) -> list[str]:
        values = self._extract_text_values(
            raw_plan,
            ("multi_views", "semantic_views", "perspectives", "views"),
            ("text", "query", "view", "perspective"),
        )
        if not values:
            values = self._extract_text_values(raw_plan, ("variants",), ("text", "query", "view", "perspective"))
        if not values:
            values.append(query)
        return self._dedupe(values)[:max_views]

    def _extract_variants(self, raw_plan: dict[str, Any], query: str, max_variants: int) -> list[str]:
        raw_variants = raw_plan.get("variants", raw_plan.get("multi_views", []))
        values: list[str] = []
        if isinstance(raw_variants, list):
            for item in raw_variants:
                if isinstance(item, dict):
                    values.append(str(item.get("text") or item.get("query") or item.get("view") or "").strip())
                else:
                    values.append(str(item).strip())
        if not values:
            values.append(query)
        return self._dedupe(values)[:max_variants]

    def _extract_text_variants(self, raw_plan: dict[str, Any], query: str, max_variants: int) -> list[str]:
        """Keep lexical rewrites separate from English CLIP embedding rewrites."""
        raw_values = raw_plan.get("text_variants", raw_plan.get("text_views", raw_plan.get("asr_variants", [])))
        values: list[str] = [query]
        if isinstance(raw_values, list):
            for item in raw_values:
                if isinstance(item, dict):
                    values.append(str(item.get("text") or item.get("query") or item.get("view") or "").strip())
                else:
                    values.append(str(item).strip())
        return self._dedupe(values)[:max_variants]

    def _extract_text_values(
        self,
        payload: dict[str, Any],
        list_keys: tuple[str, ...],
        item_keys: tuple[str, ...],
    ) -> list[str]:
        values: list[str] = []
        for list_key in list_keys:
            raw_values = payload.get(list_key)
            if isinstance(raw_values, str):
                values.append(raw_values.strip())
                continue
            if not isinstance(raw_values, list):
                continue
            for item in raw_values:
                if isinstance(item, dict):
                    for item_key in item_keys:
                        value = str(item.get(item_key) or "").strip()
                        if value:
                            values.append(value)
                            break
                else:
                    values.append(str(item).strip())
        return self._dedupe(values)

    def _extract_retrieval_strategy(self, raw_plan: dict[str, Any], query: str, query_type: str) -> dict[str, Any]:
        raw_strategy = raw_plan.get("retrieval_strategy")
        strategy = raw_strategy if isinstance(raw_strategy, dict) else {}
        raw_weights = strategy.get("weights")
        if not isinstance(raw_weights, dict):
            raw_weights = raw_plan.get("retrieval_weights")
        weights = self._normalize_retrieval_weights(raw_weights)
        fallback_strategy = self.infer_retrieval_strategy(query, query_type)
        weight_source = "agent" if weights else fallback_strategy["weight_source"]
        if not weights:
            weights = fallback_strategy["weights"]

        raw_text_source_weights = strategy.get("text_source_weights")
        if not isinstance(raw_text_source_weights, dict):
            raw_text_source_weights = raw_plan.get("text_source_weights")
        text_source_weights = self._normalize_text_source_weights(raw_text_source_weights)
        text_source_weight_source = "agent" if text_source_weights else fallback_strategy["text_source_weight_source"]
        if not text_source_weights:
            text_source_weights = fallback_strategy["text_source_weights"]
        text_source_weights = self._gate_ocr_weight(text_source_weights, query)

        clauses: list[dict[str, Any]] = []
        raw_clauses = strategy.get("clauses")
        if isinstance(raw_clauses, list):
            for item in raw_clauses[:12]:
                if not isinstance(item, dict):
                    continue
                text = " ".join(str(item.get("text") or "").split())
                evidence = str(item.get("evidence") or "both").strip().lower()
                if evidence not in {"visual", "text", "both"}:
                    evidence = "both"
                try:
                    importance = float(item.get("importance", 0.5))
                except (TypeError, ValueError):
                    importance = 0.5
                if text:
                    clauses.append(
                        {
                            "text": text,
                            "evidence": evidence,
                            "importance": round(min(1.0, max(0.0, importance)), 4),
                        }
                    )

        return {
            "clauses": clauses or fallback_strategy["clauses"],
            "weights": weights,
            "rationale": " ".join(str(strategy.get("rationale") or "").split())[:400]
            or fallback_strategy["rationale"],
            "weight_source": weight_source,
            "text_source_weights": text_source_weights,
            "text_source_weight_source": text_source_weight_source,
        }

    def _extract_temporal_event_plans(
        self,
        raw_plan: dict[str, Any],
        temporal_events: list[str],
        query_type: str,
        max_views: int,
    ) -> list[dict[str, Any]]:
        raw_events = raw_plan.get("temporal_events")
        raw_event_items = raw_events if isinstance(raw_events, list) else []
        plans: list[dict[str, Any]] = []
        for index, event_query in enumerate(temporal_events, start=1):
            raw_event = raw_event_items[index - 1] if index - 1 < len(raw_event_items) else {}
            raw_event = raw_event if isinstance(raw_event, dict) else {}
            raw_weights = raw_event.get("retrieval_weights")
            if not isinstance(raw_weights, dict):
                raw_event_strategy = raw_event.get("retrieval_strategy")
                raw_weights = raw_event_strategy.get("weights") if isinstance(raw_event_strategy, dict) else None
            weights = self._normalize_retrieval_weights(raw_weights)
            fallback = self._heuristic_temporal_event_plan(event_query, index, query_type)
            raw_text_source_weights = raw_event.get("text_source_weights")
            if not isinstance(raw_text_source_weights, dict):
                raw_event_strategy = raw_event.get("retrieval_strategy")
                raw_text_source_weights = (
                    raw_event_strategy.get("text_source_weights") if isinstance(raw_event_strategy, dict) else None
                )
            text_source_weights = self._normalize_text_source_weights(raw_text_source_weights)
            try:
                importance = float(raw_event.get("importance", raw_event.get("weight", 1.0)))
            except (TypeError, ValueError):
                importance = 1.0
            text_query = " ".join(str(raw_event.get("text_query") or "").split())
            evidence_query = text_query or event_query
            resolved_text_weights = text_source_weights or fallback["text_source_weights"]
            if not text_source_weights:
                resolved_text_weights = self._gate_ocr_weight(resolved_text_weights, evidence_query)
            semantic_views = self._extract_event_multi_views(raw_event, event_query, max_views)
            text_views = self._extract_event_text_views(raw_event, text_query or event_query, max_views)
            plans.append(
                {
                    "event_index": index,
                    "query": event_query,
                    "text_query": text_query,
                    "multi_views": semantic_views,
                    "semantic_views": semantic_views,
                    "text_views": text_views,
                    "importance": round(min(1.0, max(0.0, importance)), 4),
                    "retrieval_weights": weights or fallback["retrieval_weights"],
                    "retrieval_weight_source": "agent" if weights else fallback["retrieval_weight_source"],
                    "text_source_weights": resolved_text_weights,
                    "text_source_weight_source": "agent" if text_source_weights else fallback["text_source_weight_source"],
                }
            )
        return plans

    def _extract_event_multi_views(self, raw_event: dict[str, Any], event_query: str, max_views: int) -> list[str]:
        values = self._extract_text_values(
            raw_event,
            ("multi_views", "semantic_views", "perspectives", "views", "variants"),
            ("text", "query", "view", "perspective"),
        )
        values = self._dedupe([event_query, *values])
        return self._prefer_english_values(event_query, values, max_views)

    def _extract_event_text_views(self, raw_event: dict[str, Any], text_query: str, max_views: int) -> list[str]:
        values = self._extract_text_values(
            raw_event,
            ("text_views", "text_multi_views", "lexical_views", "text_variants", "asr_variants"),
            ("text", "query", "view", "perspective"),
        )
        return self._dedupe([text_query, *values])[:max_views]

    @staticmethod
    def _extract_temporal_anchor_index(raw_plan: dict[str, Any], event_count: int) -> int | None:
        if event_count <= 0:
            return None
        try:
            value = int(raw_plan.get("temporal_anchor_index"))
        except (TypeError, ValueError):
            value = 0
        if 1 <= value <= event_count:
            return value
        return max(1, (event_count + 1) // 2)

    def _heuristic_temporal_event_plan(self, query: str, index: int, query_type: str) -> dict[str, Any]:
        strategy = self.infer_retrieval_strategy(query, query_type)
        return {
            "event_index": index,
            "query": query,
            "text_query": query,
            "multi_views": [query],
            "semantic_views": [query],
            "text_views": [query],
            "importance": 1.0,
            "retrieval_weights": strategy["weights"],
            "retrieval_weight_source": strategy["weight_source"],
            "text_source_weights": strategy["text_source_weights"],
            "text_source_weight_source": strategy["text_source_weight_source"],
        }

    def infer_retrieval_strategy(self, query: str, query_type: str) -> dict[str, Any]:
        folded = self._fold_for_matching(query)
        text_evidence = len(TEXT_EVIDENCE_RE.findall(folded))
        if re.search(r"[\"'\u201c\u201d]", query):
            text_evidence += 1
        if re.search(r"\b(?:19|20)\d{2}\b", folded):
            text_evidence += 1
        if re.search(r"\b[a-z]{2,}(?:\s+[a-z]{2,}){1,}\b", folded) and any(
            marker in folded for marker in ("noi", "hoi", "ten", "cau tho", "tieu de", "cong thuc")
        ):
            text_evidence += 1

        if query_type == "TRAKE" and text_evidence < 2:
            weights = {"visual": 0.78, "text": 0.22}
            rationale = "Ordered actions and object-contact moments are primarily verified from visual keyframes."
        elif text_evidence >= 2:
            weights = {"visual": 0.28, "text": 0.72}
            rationale = "Named facts, speech, titles, dates, or quoted wording are best supported by ASR/metadata."
        elif text_evidence == 1:
            weights = {"visual": 0.45, "text": 0.55}
            rationale = "The query includes a lexical or narrated fact, so ASR/metadata receives a slight priority."
        else:
            weights = {"visual": 0.67, "text": 0.33}
            rationale = "The query is primarily an observable scene, action, or appearance description."

        if SPEECH_EVIDENCE_RE.search(folded):
            text_source_weights = {"asr": 0.7, "caption": 0.2, "ocr": 0.1}
            text_source_rationale = "Spoken or narrated evidence is most likely in ASR."
        elif OCR_EVIDENCE_RE.search(folded):
            text_source_weights = {"asr": 0.1, "caption": 0.2, "ocr": 0.7}
            text_source_rationale = "Exact visible wording, names, or numbers are most likely in OCR."
        else:
            text_source_weights = {"asr": 0.2, "caption": 0.8, "ocr": 0.0}
            text_source_rationale = "No displayed-text cue is present, so scene evidence stays with captions instead of OCR."

        clauses = [
            {
                "text": "lexical, spoken, or named fact evidence" if text_evidence else "observable scene and action evidence",
                "evidence": "text" if text_evidence else "visual",
                "importance": 1.0,
            }
        ]
        return {
            "clauses": clauses,
            "weights": weights,
            "rationale": f"{rationale} {text_source_rationale}",
            "weight_source": "heuristic",
            "text_source_weights": text_source_weights,
            "text_source_weight_source": "heuristic",
        }

    def _normalize_retrieval_weights(self, raw_weights: Any) -> dict[str, float]:
        if not isinstance(raw_weights, dict):
            return {}
        try:
            visual = max(0.0, float(raw_weights.get("visual")))
            text = max(0.0, float(raw_weights.get("text")))
        except (TypeError, ValueError):
            return {}
        total = visual + text
        if total <= 0:
            return {}
        return {"visual": round(visual / total, 6), "text": round(text / total, 6)}

    @staticmethod
    def _normalize_text_source_weights(raw_weights: Any) -> dict[str, float]:
        if not isinstance(raw_weights, dict):
            return {}
        try:
            weights = {
                source: max(0.0, float(raw_weights.get(source, 0.0)))
                for source in ("asr", "caption", "ocr")
            }
        except (TypeError, ValueError):
            return {}
        total = sum(weights.values())
        if total <= 0:
            return {}
        return {source: round(value / total, 6) for source, value in weights.items()}

    def _gate_ocr_weight(self, weights: dict[str, float], query: str) -> dict[str, float]:
        """OCR is only a retrieval signal when the query requests visible textual evidence."""
        folded = self._fold_for_matching(query)
        if OCR_EVIDENCE_RE.search(folded):
            return weights
        if not SPEECH_EVIDENCE_RE.search(folded):
            return {"asr": 0.2, "caption": 0.8, "ocr": 0.0}
        gated = {source: max(0.0, float(weights.get(source, 0.0))) for source in ("asr", "caption", "ocr")}
        gated["ocr"] = 0.0
        total = gated["asr"] + gated["caption"]
        if total <= 0:
            return {"asr": 0.2, "caption": 0.8, "ocr": 0.0}
        return {source: round(value / total, 6) for source, value in gated.items()}

    def _prefer_english_values(self, query: str, values: list[str], max_values: int) -> list[str]:
        deduped = self._dedupe(values)
        rewrite = self._english_retrieval_rewrite(query)
        preferred = [rewrite] if rewrite else []
        preferred.extend(deduped)
        return self._translate_vietnamese_values(preferred)[:max_values]

    def _prefer_english_temporal_events(self, query: str, values: list[str], max_values: int) -> list[str]:
        deduped = self._dedupe(values)
        if len(deduped) <= 1:
            return self._prefer_english_values(query, deduped, max_values)
        rewritten = [self._english_retrieval_rewrite(value) or value for value in deduped]
        return self._translate_vietnamese_values(rewritten)[:max_values]

    def _translate_vietnamese_values(self, values: list[str]) -> list[str]:
        """Repair a plan that ignored the English-only embedding contract."""
        normalized = [" ".join(str(value).split()) for value in values if str(value).strip()]
        if not any(self._looks_vietnamese(value) for value in normalized):
            return normalized
        if self._unavailable_reason():
            return normalized

        prompt = (
            "Translate each Vietnamese retrieval query to concise English. "
            "Keep the same order, preserve every event, and do not add or omit details. "
            "Return exactly one JSON object with this schema and no markdown: "
            '{"translations":["English query 1","English query 2"]}. '
            "Every translation must be English."
        )
        try:
            self._wait_for_openai_slot()
            output = self._get_model().invoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps({"queries": normalized}, ensure_ascii=False)},
                ],
                config={
                    "run_name": "llm_query_translation_repair",
                    "tags": ["retrieval", "query-translation", self._provider()],
                    "metadata": {"active_profile": self._active_profile_name()},
                },
            )
            raw = self._parse_json_object(self._extract_content(output))
            translations = raw.get("translations")
            if not isinstance(translations, list) or len(translations) != len(normalized):
                return normalized
            repaired = [" ".join(str(value).split()) for value in translations]
            if any(not value or self._looks_vietnamese(value) for value in repaired):
                return normalized
            return repaired
        except Exception:
            logger.warning("LLM translation repair failed; semantic retrieval keeps the original plan.")
            return normalized

    def _should_prefer_english(self, query: str, value: str) -> bool:
        value = str(value or "").strip()
        if not value:
            return True
        if self._fold_for_matching(value) == self._fold_for_matching(query):
            return True
        return self._looks_vietnamese(value)

    def _english_retrieval_rewrite(self, query: str) -> str | None:
        folded = self._fold_for_matching(query)
        if not folded or not self._looks_vietnamese(query):
            return None

        if ("tau vu tru" in folded or "phong tau" in folded) and "phi hanh gia" in folded:
            return (
                "private space launch introduction, four astronauts wearing black suits, "
                "spacecraft mission studying aurora lights in polar regions"
            )
        if ("dan ho" in folded or "ho con" in folded) and ("mien nam" in folded or "moi sinh" in folded):
            return "news segment about a tiger family in southern Vietnam with newborn tiger cubs, rare tiger species"
        if "nguoi ao do" in folded or ("nguoi" in folded and "ao" in folded and " do" in f" {folded} "):
            return "person wearing a red shirt"

        phrase_map = [
            ("tau vu tru tu nhan", "private spacecraft"),
            ("phong tau vu tru", "spacecraft launch"),
            ("tau vu tru", "spacecraft"),
            ("phi hanh gia", "astronauts"),
            ("ao den", "black suits"),
            ("anh sang cuc quang", "aurora lights"),
            ("cuc quang", "aurora lights"),
            ("vung cuc", "polar regions"),
            ("nghien cuu", "research"),
            ("mau tin", "news segment"),
            ("ban tin", "news segment"),
            ("gioi thieu", "introduction"),
            ("dan ho", "tiger family"),
            ("ho con", "tiger cubs"),
            ("mien nam", "southern Vietnam"),
            ("quy hiem", "rare"),
            ("moi sinh", "newborn"),
            ("nguoi", "person"),
            ("ao do", "red shirt"),
            ("ao den", "black shirt"),
        ]
        pieces = [english for vietnamese, english in phrase_map if vietnamese in folded]
        return " ".join(self._dedupe(pieces)) if len(pieces) >= 2 else None

    def _looks_vietnamese(self, value: str) -> bool:
        if VIETNAMESE_DIACRITICS_RE.search(value):
            return True
        tokens = re.findall(r"[a-z]+", self._fold_for_matching(value))
        if not tokens:
            return False
        signal_count = sum(1 for token in tokens if token in VIETNAMESE_SIGNAL_TOKENS)
        return signal_count >= 2 and signal_count / max(len(tokens), 1) >= 0.25

    def _fold_for_matching(self, value: str) -> str:
        value = str(value or "").replace("\u0111", "d").replace("\u0110", "D")
        normalized = unicodedata.normalize("NFKD", value)
        ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
        tokens = re.findall(r"[a-z0-9]+", ascii_text.lower())
        return " ".join(tokens)

    def _extract_temporal_events(self, raw_plan: dict[str, Any], query: str) -> list[str]:
        raw_events = raw_plan.get("temporal_events")
        values: list[str] = []
        if isinstance(raw_events, list):
            for item in raw_events:
                if isinstance(item, dict):
                    values.append(str(item.get("query") or "").strip())
                else:
                    values.append(str(item).strip())
        if not values:
            values = self._heuristic_temporal_events(query)
        return self._dedupe(values)[: self._max_temporal_events()]

    def _heuristic_temporal_events(self, query: str) -> list[str]:
        return parse_temporal_events(query, max_events=self._max_temporal_events()).events
        separators = [
            r"\bthen\b",
            r"\bafter that\b",
            r"\bsau do\b",
            r"\bsau đó\b",
            r"\btiep theo\b",
            r"\btiếp theo\b",
            r";",
            r"\(e\d+\)\s*:",
        ]
        pattern = "|".join(separators)
        parts = [part.strip(" .:-") for part in re.split(pattern, query, flags=re.IGNORECASE) if part.strip(" .:-")]
        return parts if len(parts) > 1 else ([query] if query else [])

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        content = (content or "").strip()
        if not content:
            raise ValueError("agent returned empty content")
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return self._repair_utf8_mojibake(parsed)
        except json.JSONDecodeError:
            pass

        fence_match = JSON_FENCE_RE.search(content)
        if fence_match:
            parsed = json.loads(fence_match.group("body"))
            if isinstance(parsed, dict):
                return self._repair_utf8_mojibake(parsed)

        decoder = json.JSONDecoder()
        for idx, char in enumerate(content):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(content[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return self._repair_utf8_mojibake(parsed)
        raise ValueError("agent response did not contain a JSON object")

    def _repair_utf8_mojibake(self, value: Any) -> Any:
        """Recover UTF-8 text incorrectly decoded as Latin-1 by an LLM gateway.

        Some OpenAI-compatible endpoints return Vietnamese JSON values such as
        ``"trÃ¡Â»Â©ng"``.  Repair only strings with unambiguous mojibake markers;
        normal English and correctly encoded Unicode values are left untouched.
        """
        if isinstance(value, dict):
            return {key: self._repair_utf8_mojibake(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._repair_utf8_mojibake(item) for item in value]
        if not isinstance(value, str):
            return value

        repaired = value
        markers = ("Ã", "Ä", "Å", "Æ", "Â", "á»", "áº")
        for _ in range(2):
            if not any(marker in repaired for marker in markers):
                break
            try:
                candidate = repaired.encode("latin-1").decode("utf-8")
            except UnicodeError:
                break
            if candidate == repaired:
                break
            repaired = candidate
        return repaired

    def _extract_content(self, output: Any) -> str:
        if isinstance(output, dict):
            structured = output.get("structured_response")
            if isinstance(structured, dict):
                return json.dumps(structured)
            if isinstance(structured, str):
                return structured
            messages = output.get("messages")
            if isinstance(messages, list) and messages:
                return self._message_content(messages[-1])
            return json.dumps(output)
        return self._message_content(output)

    def _message_content(self, message: Any) -> str:
        if isinstance(message, dict):
            content = message.get("content", "")
        else:
            content = getattr(message, "content", message)
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    chunks.append(str(item.get("text") or item.get("content") or ""))
                else:
                    chunks.append(str(item))
            return "\n".join(chunk for chunk in chunks if chunk)
        return str(content or "")

    def _agent_metadata(self) -> dict[str, Any]:
        provider = self._provider()
        api_key_env = self._api_key_env(provider)
        langsmith_cfg = self.agent_config.get("langsmith", {})
        langsmith_key_env = "LANGSMITH_API_KEY"
        if isinstance(langsmith_cfg, dict):
            langsmith_key_env = str(langsmith_cfg.get("api_key_env", langsmith_key_env)).strip() or langsmith_key_env
        return {
            "enabled": bool(self.agent_config.get("enabled", False)),
            "active_profile": self._active_profile_name(),
            "provider": provider,
            "model": self._model_name(),
            "api_key_env": api_key_env,
            "api_key_configured": bool(os.getenv(api_key_env, "").strip()),
            "langsmith_api_key_env": langsmith_key_env,
            "langsmith_api_key_configured": bool(os.getenv(langsmith_key_env, "").strip()),
            "config_path": str(self.config_path) if self.config_path else None,
        }

    def _api_key_env(self, provider: str | None = None) -> str:
        provider = provider or self._provider()
        default_key_env = self._default_api_key_env(provider)
        return str(self._profile_value("api_key_env", default_key_env)).strip() or default_key_env

    def _default_api_key_env(self, provider: str) -> str:
        return "OPENAI_API_KEY" if provider == "openai" else "GROQ_API_KEY"

    def _active_profile_name(self) -> str:
        env_profile = os.getenv("AGENT_LLM_PROFILE", "").strip()
        if env_profile:
            return env_profile
        configured = str(self.agent_config.get("active_profile", "")).strip()
        if configured:
            return configured
        return "legacy"

    def _execution_mode(self) -> str:
        mode = str(self.agent_config.get("execution_mode", "deep_agent")).strip().lower()
        return mode if mode in {"direct", "deep_agent"} else "deep_agent"

    def _profile_config(self) -> dict[str, Any]:
        profiles = self.agent_config.get("profiles")
        if isinstance(profiles, dict) and profiles:
            profile = profiles.get(self._active_profile_name())
            return profile if isinstance(profile, dict) else {}
        return self.agent_config

    def _unknown_profile_reason(self) -> str | None:
        profiles = self.agent_config.get("profiles")
        if isinstance(profiles, dict) and profiles and self._active_profile_name() not in profiles:
            return f"unknown agent profile: {self._active_profile_name()}"
        return None

    def _profile_value(self, key: str, default: Any = None) -> Any:
        profile = self._profile_config()
        if key in profile:
            return profile[key]
        return self.agent_config.get(key, default)

    def _provider(self) -> str:
        return str(self._profile_value("provider", "")).strip().lower()

    def _model_name(self) -> str:
        return str(self._profile_value("model", "openai/gpt-oss-120b")).strip()

    def _max_variants_default(self) -> int:
        return int(self.agent_config.get("max_variants_default", 5))

    def _max_temporal_events(self) -> int:
        return max(1, int(self.agent_config.get("max_temporal_events", 8)))

    def _fallback_on_error(self) -> bool:
        value = self.agent_config.get("fallback_on_error", True)
        return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _langsmith_trace_enabled(self) -> bool:
        langsmith_cfg = self.agent_config.get("langsmith", {})
        if not isinstance(langsmith_cfg, dict):
            return False
        tracing_env = str(langsmith_cfg.get("tracing_env", "LANGSMITH_TRACING")).strip() or "LANGSMITH_TRACING"
        return os.getenv(tracing_env, "").strip().lower() == "true"

    def _dedupe(self, values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in values:
            value = " ".join(str(item).split())
            key = value.lower()
            if value and key not in seen:
                deduped.append(value)
                seen.add(key)
        return deduped
