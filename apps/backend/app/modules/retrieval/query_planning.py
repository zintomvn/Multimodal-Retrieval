from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)


@dataclass
class QueryPlanningResult:
    language: str = "auto"
    intent: str = "FREEFORM"
    summary: str = ""
    variants: list[str] = field(default_factory=list)
    temporal_events: list[str] = field(default_factory=list)
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
            "variants": self.variants,
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

    def __init__(self, config: dict[str, Any], config_path: Path | None = None) -> None:
        self.config = config
        self.config_path = config_path
        self.agent_config = config.get("llm_query_planning", {}) if isinstance(config, dict) else {}
        self._agents: dict[str, Any] = {}

    @classmethod
    def from_config(cls, path: Path) -> "AgentQueryPlanner":
        if not path.exists():
            return cls(config={}, config_path=path)
        with path.open("r", encoding="utf-8") as handle:
            return cls(config=yaml.safe_load(handle) or {}, config_path=path)

    def plan(self, query: str, query_type: str, max_variants: int) -> QueryPlanningResult:
        query = query.strip()
        max_variants = max(1, int(max_variants or self._max_variants_default()))
        if not query:
            return self._fallback_plan(query, query_type, max_variants, error="blank query")

        unavailable_reason = self._unavailable_reason()
        if unavailable_reason:
            return self._fallback_plan(query, query_type, max_variants, error=unavailable_reason)

        try:
            output = self._invoke_agent(query=query, query_type=query_type, max_variants=max_variants)
            content = self._extract_content(output)
            raw_plan = self._parse_json_object(content)
            result = self._result_from_raw_plan(
                raw_plan=raw_plan,
                query=query,
                query_type=query_type,
                max_variants=max_variants,
            )
            if result.variants:
                return result
            return self._fallback_plan(query, query_type, max_variants, error="agent returned no variants")
        except Exception as exc:  # pragma: no cover - exercised in integration environments.
            logger.warning("LLM query planning failed; falling back to baseline expansion.", exc_info=True)
            if self._fallback_on_error():
                return self._fallback_plan(query, query_type, max_variants, error=str(exc))
            raise

    def _invoke_agent(self, query: str, query_type: str, max_variants: int) -> Any:
        agent = self._get_agent()
        user_payload = {
            "query_text": query,
            "query_type": query_type,
            "max_variants": max_variants,
            "max_temporal_events": self._max_temporal_events(),
            "task": "Return the JSON query plan only.",
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
        trace_enabled = self._configure_langsmith()
        try:
            import langsmith as ls  # type: ignore
        except ImportError:
            return agent.invoke({"messages": [{"role": "user", "content": json.dumps(user_payload)}]}, config=runnable_config)

        with ls.tracing_context(enabled=trace_enabled):
            return agent.invoke({"messages": [{"role": "user", "content": json.dumps(user_payload)}]}, config=runnable_config)

    def _get_agent(self) -> Any:
        active_profile = self._active_profile_name()
        if active_profile in self._agents:
            return self._agents[active_profile]

        from deepagents import create_deep_agent  # type: ignore

        llm = self._create_chat_model()
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

    def _create_chat_model(self) -> Any:
        provider = self._provider()
        if provider == "chatgroq":
            from langchain_groq import ChatGroq  # type: ignore

            model_kwargs = self._chat_model_kwargs(token_key="max_tokens")
            reasoning_format = str(self._profile_value("reasoning_format", "")).strip()
            if reasoning_format:
                model_kwargs["reasoning_format"] = reasoning_format
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
        return model_kwargs

    def _unavailable_reason(self) -> str | None:
        if not bool(self.agent_config.get("enabled", False)):
            return "agent query planning is disabled"
        if self._unknown_profile_reason():
            return self._unknown_profile_reason()

        provider = self._provider()
        if provider not in {"chatgroq", "openai"}:
            return f"unsupported agent provider: {provider}"

        default_key_env = "OPENAI_API_KEY" if provider == "openai" else "GROQ_API_KEY"
        api_key_env = str(self._profile_value("api_key_env", default_key_env)).strip() or default_key_env
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

        temporal_events = self._extract_temporal_events(raw_plan, query)
        variants = self._extract_variants(raw_plan, query, max_variants)
        if len(variants) < max_variants:
            variants = self._dedupe(variants + temporal_events + self._factor_variants(search_factors))[:max_variants]

        metadata = self._agent_metadata()
        metadata["langsmith_trace_enabled"] = self._langsmith_trace_enabled()

        return QueryPlanningResult(
            language=str(raw_plan.get("language") or "auto"),
            intent=str(raw_plan.get("intent") or query_type),
            summary=str(raw_plan.get("summary") or ""),
            variants=variants,
            temporal_events=temporal_events,
            decomposition={
                "search_factors": search_factors,
                "raw_temporal_events": raw_plan.get("temporal_events") if isinstance(raw_plan.get("temporal_events"), list) else [],
            },
            agent_metadata=metadata,
            source="langchain_deep_agent",
        )

    def _fallback_plan(self, query: str, query_type: str, max_variants: int, error: str | None = None) -> QueryPlanningResult:
        temporal_events = self._heuristic_temporal_events(query)
        tokens = [token.lower() for token in re.findall(r"[\w]+", query, flags=re.UNICODE) if len(token) > 1]
        return QueryPlanningResult(
            language="auto",
            intent=query_type,
            summary=query,
            variants=[query] if query else [],
            temporal_events=temporal_events[: self._max_temporal_events()],
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
                "raw_temporal_events": [],
            },
            agent_metadata=self._agent_metadata(),
            source="fallback",
            error=error,
        )

    def _extract_variants(self, raw_plan: dict[str, Any], query: str, max_variants: int) -> list[str]:
        raw_variants = raw_plan.get("variants", [])
        values: list[str] = [query]
        if isinstance(raw_variants, list):
            for item in raw_variants:
                if isinstance(item, dict):
                    values.append(str(item.get("text") or "").strip())
                else:
                    values.append(str(item).strip())
        return self._dedupe(values)[:max_variants]

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

    def _factor_variants(self, search_factors: dict[str, Any]) -> list[str]:
        variants: list[str] = []
        for key in ("subjects", "actions", "objects", "attributes", "scene", "text_cues"):
            values = search_factors.get(key)
            if isinstance(values, list):
                text = " ".join(str(value).strip() for value in values[:6] if str(value).strip())
                if text:
                    variants.append(text)
        return variants

    def _heuristic_temporal_events(self, query: str) -> list[str]:
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
                return parsed
        except json.JSONDecodeError:
            pass

        fence_match = JSON_FENCE_RE.search(content)
        if fence_match:
            parsed = json.loads(fence_match.group("body"))
            if isinstance(parsed, dict):
                return parsed

        decoder = json.JSONDecoder()
        for idx, char in enumerate(content):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(content[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("agent response did not contain a JSON object")

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
        return {
            "active_profile": self._active_profile_name(),
            "provider": self._provider(),
            "model": self._model_name(),
            "config_path": str(self.config_path) if self.config_path else None,
        }

    def _active_profile_name(self) -> str:
        env_profile = os.getenv("AGENT_LLM_PROFILE", "").strip()
        if env_profile:
            return env_profile
        configured = str(self.agent_config.get("active_profile", "")).strip()
        if configured:
            return configured
        return "legacy"

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
