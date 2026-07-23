# LLM Query Expansion and Decomposition Technical Report

## Scope

This milestone adds an agent-based query planning layer before the existing hybrid
retrieval pipeline. The goal is to transform a raw user query into:

- retrieval variants for semantic vector search and metadata search;
- decomposed visual factors such as subjects, actions, objects, attributes,
  scene cues, OCR/text cues, and temporal cues;
- ordered temporal event queries for TRAKE-style sequence retrieval.

The existing search contract remains compatible. A simple query can still be
submitted to `POST /api/retrieval/search` with the previous payload shape.

## Implementation Summary

New and changed files:

- `configs/agent.yaml`: agent enablement, Groq/OpenAI model profiles,
  hyperparameters, LangSmith tracing settings, and system prompts for each agent.
- `apps/backend/app/modules/retrieval/query_planning.py`: optional LangChain
  Deep Agents planner with ChatGroq, ChatOpenAI, and deterministic fallback.
- `apps/backend/app/modules/retrieval/service.py`: wires the agent plan into
  `normalized_query.variants` and `normalized_query.temporal_events`.
- `apps/backend/app/modules/retrieval/schemas.py`: adds
  `SearchOptions.use_agent_query_planning`, defaulting to `true`.
- `.env.example`: documents `AGENT_CONFIG_PATH`, `AGENT_LLM_PROFILE`,
  `GROQ_API_KEY`, `OPENAI_API_KEY`, and LangSmith tracing variables.
- `apps/backend/tests/test_retrieval_pipeline.py`: adds simple-query fallback,
  OpenAI-profile fallback, and profile override smoke tests.

## Agent Architecture

The planner uses LangChain Deep Agents through `deepagents.create_deep_agent`.
The root planner agent orchestrates two specialized subagents:

- `query_decomposition_agent`: extracts observable search factors from the
  query.
- `query_expansion_agent`: creates short retrieval rewrites for vector and
  metadata backends.

The default configured model profile is ChatGroq:

```yaml
active_profile: groq_gpt_oss_120b
profiles:
  groq_gpt_oss_120b:
    provider: chatgroq
    model: openai/gpt-oss-120b
    api_key_env: GROQ_API_KEY
    reasoning_format: parsed
  openai_gpt4o:
    provider: openai
    model: gpt-4o
    api_key_env: OPENAI_API_KEY
```

Use `AGENT_LLM_PROFILE=openai_gpt4o` to route the same Deep Agents workflow
through OpenAI `gpt-4o`. The OpenAI profile maps `max_tokens` from YAML to
LangChain `ChatOpenAI(max_completion_tokens=...)`.

Current provider profiles:

| Profile | Provider | Model | API key env | Token parameter | Intended use |
| --- | --- | --- | --- | --- | --- |
| `groq_gpt_oss_120b` | `chatgroq` | `openai/gpt-oss-120b` | `GROQ_API_KEY` | `max_tokens` | Default low-latency text-only query planning. |
| `openai_gpt4o` | `openai` | `gpt-4o` | `OPENAI_API_KEY` | `max_completion_tokens` | OpenAI API routing, stronger compatibility with structured outputs, and future multimodal planning. |

The planner requests one strict JSON object. The retrieval service parses this
object, de-duplicates variants, and forwards the resulting text queries into the
existing Milvus and Elasticsearch scoring flow. For TRAKE and ordered queries,
`temporal_events[*].query` becomes the event list consumed by adaptive temporal
search.

Profile resolution order:

1. `AGENT_LLM_PROFILE` environment variable, when set.
2. `llm_query_planning.active_profile` in `configs/agent.yaml`.
3. Legacy top-level provider fields, for backward compatibility with the first
   single-provider YAML shape.

## Runtime Behavior

When `SearchOptions.use_query_expansion=true` and the retrieval profile enables
query expansion, the service attempts agent planning first.

If the agent succeeds:

- `normalized_query.variants` comes from the agent expansion variants;
- `normalized_query.temporal_events` comes from the agent decomposition;
- `normalized_query.agent_query_plan.source` is `langchain_deep_agent`.

If the agent cannot run because the selected provider API key is missing,
dependencies are not installed, or the remote call fails:

- the system falls back to the existing `model_registry.query_expander`;
- `normalized_query.agent_query_plan.source` is `fallback`;
- `normalized_query.agent_query_plan.error` records the fallback reason;
- search still proceeds for simple queries.

## LangSmith Tracing

Tracing is configured in `configs/agent.yaml` and activated only when a
LangSmith API key is available. The service sets:

- `LANGSMITH_TRACING=true`
- `LANGSMITH_PROJECT=multimodal-retrieval-query-agents`
- `LANGSMITH_ENDPOINT=https://api.smith.langchain.com`

Agent invocations include tags `retrieval`, `query-planning`, and `chatgroq`,
or `openai`, plus metadata for active profile, provider, model, query type, and
config path.

## Configuration

Primary configuration file:

```text
configs/agent.yaml
```

Environment variables:

```powershell
$env:AGENT_CONFIG_PATH="./configs/agent.yaml"
$env:AGENT_LLM_PROFILE="groq_gpt_oss_120b"
$env:GROQ_API_KEY="<your-groq-api-key>"
$env:OPENAI_API_KEY="<your-openai-api-key>"
$env:LANGSMITH_TRACING="true"
$env:LANGSMITH_API_KEY="<your-langsmith-api-key>"
$env:LANGSMITH_PROJECT="multimodal-retrieval-query-agents"
```

Switch to OpenAI for the current shell:

```powershell
$env:AGENT_LLM_PROFILE="openai_gpt4o"
```

Dependencies:

```text
deepagents>=0.5.3
langchain-groq>=1.0.0
langchain-openai>=1.0.0
langsmith>=0.4.0
```

## Provider Decision

Groq remains the default for the current text-only planning step because the
Groq model page publishes `openai/gpt-oss-120b` at roughly 500 tokens per second.
OpenAI `gpt-4o` is added as a first-class profile because it is available through
OpenAI API endpoints, supports structured outputs/function calling, and provides
a better future path if the planner later consumes image inputs.

This comparison is not a controlled benchmark across both providers. It is a
configuration decision based on the public Groq token-speed number and the OpenAI
model capabilities documented for `gpt-4o`. A production benchmark should measure
end-to-end latency in LangSmith for the exact prompt, output schema, region, and
account rate limits used by this backend.

## Simple Query Smoke Test

Example request:

```powershell
$dataset=(Invoke-RestMethod http://localhost:8000/api/datasets).datasets[0].id
$body=@{
  dataset_id=$dataset
  query_name="agent-smoke-kis"
  query_type="KIS"
  query_text="nguoi ao do"
  top_k=3
  profile="competition_default"
  options=@{
    use_query_expansion=$true
    use_agent_query_planning=$true
    use_metadata=$true
  }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/retrieval/search `
  -ContentType "application/json" `
  -Body $body
```

Expected response fields:

```json
{
  "normalized_query": {
    "variants": ["nguoi ao do", "..."],
    "temporal_events": ["nguoi ao do"],
    "agent_query_plan": {
      "source": "langchain_deep_agent",
      "agent_metadata": {
        "active_profile": "groq_gpt_oss_120b",
        "provider": "chatgroq",
        "model": "openai/gpt-oss-120b"
      }
    }
  }
}
```

If the selected profile API key is not configured, the same request still runs
and `agent_query_plan.source` becomes `fallback`.

## Verification

Added automated coverage:

```powershell
.\.venv\Scripts\python.exe -m pytest apps\backend\tests\test_retrieval_pipeline.py -q
```

The new test removes `GROQ_API_KEY`, calls a simple KIS query with agent planning
enabled, and verifies that retrieval returns results through the fallback path.
It also covers `AGENT_LLM_PROFILE=openai_gpt4o` with `OPENAI_API_KEY` unset, and
checks that metadata resolves to `active_profile=openai_gpt4o`,
`provider=openai`, and `model=gpt-4o`.

Current workspace verification also ran:

```powershell
python -m py_compile apps\backend\app\core\config.py apps\backend\app\modules\retrieval\schemas.py apps\backend\app\modules\retrieval\service.py apps\backend\app\modules\retrieval\query_planning.py apps\backend\tests\test_retrieval_pipeline.py
```

Direct planner smoke with default Groq profile and `GROQ_API_KEY` unset:

```text
source=fallback
active_profile=groq_gpt_oss_120b
provider=chatgroq
model=openai/gpt-oss-120b
error=missing GROQ_API_KEY
```

Direct planner smoke with `AGENT_LLM_PROFILE=openai_gpt4o` and
`OPENAI_API_KEY` unset:

```text
source=fallback
active_profile=openai_gpt4o
provider=openai
model=gpt-4o
error=missing OPENAI_API_KEY
```

Direct retrieval normalization smoke with `query_text="nguoi ao do"` and
`AGENT_LLM_PROFILE=openai_gpt4o` confirmed the same OpenAI fallback metadata in
`normalized_query.agent_query_plan`.

Full `pytest` could not complete in this Windows workspace because pytest creates
its `basetemp` directory with ACLs that later raise `WinError 5 Access is denied`
during session cleanup. The temp directory created during the attempt was removed
after verifying its path stayed inside the repository.

## References

- LangChain Deep Agents customization:
  https://docs.langchain.com/oss/python/deepagents/customization
- LangChain ChatGroq integration:
  https://docs.langchain.com/oss/python/integrations/chat/groq
- LangChain ChatOpenAI integration:
  https://docs.langchain.com/oss/python/integrations/chat/openai
- OpenAI GPT-4o API model page:
  https://developers.openai.com/api/docs/models/gpt-4o
- Groq GPT-OSS 120B model page:
  https://console.groq.com/docs/model/openai/gpt-oss-120b
- LangSmith tracing with LangChain:
  https://docs.langchain.com/langsmith/trace-with-langchain
