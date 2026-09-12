# Technical Report — Query Planning Agent v2

**System:** Multimodal Video Retrieval System  
**Milestone:** 5  
**Scope:** LLM query planning, multi-view expansion, modality routing, and temporal event planning  
**Configuration:** configs/agent.yaml  
**Implementation:** apps/backend/app/modules/retrieval/query_planning.py

---

## 1. Executive summary

Agent v2 is the controlled planning layer between a natural-language query
(Vietnamese by default) and the retrieval engine. It does not answer the user.
It emits a structured plan that independently drives visual embedding search,
caption/ASR/OCR lexical search, reciprocal-rank fusion, and temporal sequence
reranking.

| Capability | Current behavior |
|---|---|
| Query decomposition | Identifies subjects, actions, objects, attributes, scene, lexical evidence, temporal cues, and negative constraints. |
| Multi-view generation | Produces concise English semantic views for CLIP/SigLIP2 and captions, plus Vietnamese lexical views for ASR/OCR. |
| Evidence routing | Chooses normalized visual/text and ASR/caption/OCR weights from query evidence. |
| Temporal planning | Decomposes Temporal KIS and TRAKE into up to eight ordered, independently retrievable events. |
| Resilience | Validates and normalizes LLM output, repairs collapsed temporal plans, and falls back deterministically. |

The configured runtime mode is **direct** with GPT-4o. The root planner is
therefore one direct structured LLM call. The two configured subagent prompts
are not independently invoked unless execution_mode changes to deep_agent.

---

## 2. API contract and output schema

### 2.1 Search inputs that affect planning

| Input | Type / default | Effect |
|---|---|---|
| query_type | KIS, QA, TRAKE, IMAGE, FREEFORM | Determines intent and retrieval path. |
| query_text | Non-empty string | Original user query. |
| use_agent_query_planning | Boolean, true | Enables planning for normal search. |
| temporal_mode | Boolean, false | Enables Temporal KIS when type is KIS. |
| temporal_strategy | vortex_k_context | Selects Vortex context reranking or weighted ATS. |
| temporal_anchor_index | Optional, 1–8 | Overrides target event in a temporal chain. |
| delta_t_max_ms | 180000 ms | Maximum temporal alignment window. |

Temporal KIS invokes the planner even when use_agent_query_planning is false,
because independent event plans and an anchor are needed by the temporal path.

### 2.2 Normalized output contract

The planner returns language, intent, summary, multi_views, text_variants,
temporal_events, temporal_anchor_index, retrieval_weights,
text_source_weights, temporal_event_plans, decomposition, agent_metadata,
source, and an optional error.

Example:

    {
      "language": "vi",
      "intent": "KIS",
      "summary": "person wearing a red shirt",
      "multi_views": ["person wearing a red shirt"],
      "text_variants": ["người áo đỏ"],
      "temporal_events": ["person wearing a red shirt"],
      "temporal_anchor_index": 1,
      "retrieval_weights": {"visual": 0.67, "text": 0.33},
      "text_source_weights": {"asr": 0.20, "caption": 0.80, "ocr": 0.00},
      "source": "langchain_direct_llm"
    }

The system intentionally keeps two query spaces separate:

- multi_views, semantic_views, and event query are English for visual encoders
  and English captions;
- text_variants, text_query, and text_views preserve Vietnamese lexical
  evidence for ASR/OCR/Elasticsearch.

This avoids feeding only raw Vietnamese text to an English-oriented visual
embedding index while preserving exact names, numbers, spoken phrases, and
on-screen strings for lexical search.

---

## 3. Runtime architecture

    SearchRequest
      -> RetrievalService._normalize_query
      -> AgentQueryPlanner.plan, when enabled or Temporal KIS
      -> JSON parse, validation, normalization, English repair
      -> optional temporal-plan repair
      -> normalized query
      -> independent view/event retrieval
      -> visual-text fusion
      -> Vortex context reranker or weighted ATS

Temporal event precedence is:

1. Explicit options.temporal_events from the user;
2. Agent events;
3. Deterministic temporal parser;
4. Full query as one event.

If a Temporal KIS parser finds more events than the agent returned, backend
keeps the parser output rather than letting an LLM plan collapse chronology.
The context pattern target after A and before B becomes A → target → B.

### 3.1 Direct mode versus Deep Agent mode

| Component | Current setting | Actual runtime behavior |
|---|---|---|
| execution_mode | direct | System prompt plus JSON user payload is sent directly to one model. |
| max_agent_iterations | 1 | No task loop in direct mode; only affects recursion limit in deep-agent mode. |
| Agent tools | Empty list | No filesystem, shell, or external tools are available. |
| Decomposition and expansion subagents | Configured | Materialized only in deep_agent mode. |
| Temporal repair | Conditional second model call | Runs only for Temporal KIS with temporal cues. |

This distinction is material: the current implementation is a direct planner,
not a live multi-agent delegation loop. The root prompt performs decomposition
and expansion in one response.

---

## 4. Model profiles, limits, cache, and tracing

| Profile | Provider / model | Temperature | Token cap | Timeout | Retry | Extra control |
|---|---|---:|---:|---:|---:|---|
| openai_gpt4o, active | OpenAI / gpt-4o | 0.05 | 2048 | 30 s | 5 | Minimum 5.0 s per provider:model |
| groq_gpt_oss_120b | ChatGroq / openai/gpt-oss-120b | 0.05 | 2048 | 30 s | 2 | reasoning_format parsed; reasoning_effort low |

AGENT_LLM_PROFILE overrides the configured active profile. API keys are only
read from the profile environment variables OPENAI_API_KEY or GROQ_API_KEY;
they are not returned in query plans.

Successful non-fallback plans use a process-local cache with TTL **600 seconds**.
The cache key is:

    (active_profile, query_type, exact_query, max_variants, temporal_kis)

The OpenAI interval gate uses a shared in-process lock keyed by provider:model.
It serializes concurrent calls that would violate the 5-second interval. Both
cache and rate gate are local to one backend process; they are not distributed
across replicas.

LangSmith is configured with project multimodal-retrieval-query-agents. Tracing
is enabled only if a LangSmith key exists and LANGSMITH_TRACING equals true.
The primary run is named llm_query_planning and carries tags retrieval,
query-planning, and provider. Temporal repair additionally carries the
temporal-repair tag.

---

## 5. System prompts and responsibilities

### 5.1 Root planner: query_planner_agent

The active direct call uses the following system prompt core:

    You are the root query-planning agent for a multimodal video retrieval backend.
    Transform each user query into a compact JSON search plan that downstream
    vector and metadata search can execute. Use the query_decomposition_agent
    to identify search factors and the query_expansion_agent to create concise
    AIThena-style multi-view retrieval rewrites. Do not call filesystem or shell tools.

    Return exactly one JSON object with the planning schema and no markdown.

The schema requires:

    {
      "language": "auto|vi|en|mixed",
      "intent": "KIS|QA|TRAKE|IMAGE|FREEFORM",
      "summary": "one short sentence describing the target visual moment",
      "search_factors": {
        "subjects": [], "actions": [], "objects": [], "attributes": [],
        "scene": [], "text_cues": [], "time_cues": [],
        "negative_constraints": []
      },
      "retrieval_strategy": {
        "clauses": [{"text": "atomic requirement", "evidence": "visual|text|both", "importance": 0.0}],
        "weights": {"visual": 0.0, "text": 0.0},
        "text_source_weights": {"asr": 0.0, "caption": 0.0, "ocr": 0.0},
        "rationale": "short evidence-based explanation"
      },
      "temporal_events": [{
        "order": 1, "query": "English standalone event",
        "text_query": "Vietnamese ASR/OCR event query",
        "multi_views": ["English event-level rewrite"],
        "text_views": ["Vietnamese lexical rewrite"],
        "must_have": [], "importance": 1.0,
        "retrieval_weights": {"visual": 0.0, "text": 0.0},
        "text_source_weights": {"asr": 0.0, "caption": 0.0, "ocr": 0.0}
      }],
      "temporal_anchor_index": 1,
      "multi_views": [{"text": "concise rewrite", "perspective": "literal|visual|object|caption|ocr|temporal"}],
      "text_variants": ["short Vietnamese lexical phrase"]
    }

Effective planner instructions are:

- Preserve user meaning; never invent entities, brands, or text.
- Split into atomic clauses. Visual plus text weights must be non-negative and
  sum exactly to one. ASR plus caption plus OCR weights must also sum to one.
- Route people, actions, colour, layout, and scene to visual. Route dialogue,
  names, numbers, quoted facts, and lexical questions to text.
- OCR is for an explicit request for visible words, proper names, tables,
  symbols, signs, codes, prices, dates, logos, or text on screen.
- Summary, semantic event query, and semantic multi-view text must be English.
  Vietnamese remains on the ASR/OCR lexical side.
- Every multi-view rewrite must have fewer than **24 words**.
- For Temporal KIS, split 2–8 supported chronological events. A repeated visual
  reveal is multiple events, not one abstract count. Every event gets English
  semantic views, Vietnamese lexical views, evidence weights, and importance.
- For KIS or QA without temporal mode, return one target event. For explicit
  TRAKE E1 through En, return exactly n events in the same order.

### 5.2 Decomposition subagent: query_decomposition_agent

    You decompose natural-language video retrieval queries into observable
    search factors. For each factor, decide whether its evidence is visual,
    text (ASR/OCR/caption/metadata), or both. Focus on what can be found in
    keyframes: subjects, actions, objects, colors, clothing, scene, OCR text,
    spoken words, and temporal ordering. Keep ambiguous details as optional
    factors, and do not infer facts not stated by the user.

Its contract is to identify only observable or searchable evidence. It must
keep ambiguous information optional and must not manufacture facts.

### 5.3 Expansion subagent: query_expansion_agent

    You create multi-view rewrites for hybrid video retrieval.
    Views should help semantic embeddings, captions, and metadata search.
    For temporal KIS or TRAKE, create views after event decomposition and keep
    them attached to the corresponding event. Produce short English rewrites
    by default, translating Vietnamese descriptions into concrete visual
    English while preserving exact OCR strings when needed. Rewrites combine
    the most important visual factors, OCR cues, object names, and event-level
    wording. Avoid long explanations, synonyms that change meaning, and broad
    generic words.

In active direct mode, these two subagent prompts document specialized roles
but are not separately executed. In deep_agent mode, code registers both with
empty tool lists under the root agent.

### 5.4 Dynamic temporal-plan repair prompt

For Temporal KIS queries containing temporal cues such as before, after, then,
sau khi, trước khi, tiếp theo, cuối cùng, bắt đầu, or rồi, a second model call
reviews the initial plan:

    Validate and, if needed, replace the prior Temporal KIS plan using only the user
    query. Return an independently retrievable 2-8 event chronological chain. Split
    every distinct visual transition or stated repeated reveal into its own event; do
    not collapse repetitions into a count. Infer concrete English embedding/caption
    queries and Vietnamese ASR/OCR text queries from the supplied query, without using
    fixed vocabularies or examples. Set the 1-based anchor to the intended target event.
    Return JSON only.

Repair replaces the initial plan when it has at least two events, or when the
initial plan had fewer than two. Metadata records temporal_repair_applied=true.

### 5.5 Dynamic English translation repair prompt

If a supposed semantic view remains Vietnamese after parsing, runtime can ask:

    Translate each Vietnamese retrieval query to concise English. Keep the same order,
    preserve every event, and do not add or omit details. Return exactly one JSON object
    with this schema and no markdown: {"translations":["English query 1"]}.
    Every translation must be English.

Translation is accepted only when item count matches, values are non-empty, and
Vietnamese detection no longer flags them. Otherwise the original values remain.

---

## 6. Validation, safety, and deterministic fallback

The response parser accepts plain JSON, fenced JSON, or the first recoverable
JSON object. It then:

1. Normalizes whitespace and deduplicates case-insensitively;
2. Caps variants at profile maximum and temporal events at **8**;
3. Normalizes non-negative modality and source weights to sum one;
4. Clamps clause/event importance to [0, 1];
5. Replaces an invalid anchor with the middle event, then clamps it to range;
6. Gates OCR to zero unless explicit displayed-text evidence is present.

Fallback activates if planning is disabled, a profile/key/dependency is missing,
the provider fails, parsing returns no valid semantic variants, or an exception
occurs while fallback_on_error is true. It uses local temporal parsing and the
following routing heuristics:

| Condition | Visual | Text |
|---|---:|---:|
| TRAKE and fewer than two text-evidence cues | 0.78 | 0.22 |
| Two or more text-evidence cues | 0.28 | 0.72 |
| One text-evidence cue | 0.45 | 0.55 |
| Observable visual scene/action default | 0.67 | 0.33 |

| Evidence type | ASR | Caption | OCR |
|---|---:|---:|---:|
| Speech/narration, before OCR gate | 0.70 | 0.20 | 0.10 |
| Explicit OCR/displayed text | 0.10 | 0.20 | 0.70 |
| Ordinary visual scene/action | 0.20 | 0.80 | 0.00 |

The local parser supports, in order: target-after-before context chains, E1 /
Event / Step labels, numbered lines, English and Vietnamese soft separators,
then a single event. It preserves sequence order even if the LLM is unavailable.

---

## 7. Retrieval consumption and numerical behavior

Each event has at most **8 semantic views** and **8 text views**. Semantic views
are retrieved independently, then merged as:

    S_view = 0.58*S_best + 0.24*mean(S) + 0.13*RRF_view + 0.05*coverage

where coverage is matched_views divided by total_views. The returned evidence
includes view index, semantic/text query, rank, and score.

Temporal candidate sizing and timing are:

    per_event_top_k = clamp(top_k * 20, 80, 500)
    delta_frame_max = max(1, int(delta_t_max_ms / 1000 * 30))

At the default 180000 ms, the temporal window is 5400 frames under the current
30 FPS assumption. Event importance is rescaled so total event weight equals
the event count.

- vortex_k_context uses the target anchor, compactness, and gap penalty.
- aithena_weighted_ats permits partial chains; default minimum match is
  max(2, ceil(0.6 * event_count)) and complete chains are preferred.
- Final Temporal KIS results must contain the anchor event.

Competition-default profile parameters relevant to planning:

| Parameter | Value |
|---|---:|
| Query expansion maximum | 5 |
| Semantic / metadata base weight | 0.60 / 0.30 |
| Milvus top-k per model / final top-k | 100 / 50 |
| RRF k | 60 |
| Temporal delta window | 180000 ms |
| Per-query video temporal limit | 24 |
| Compactness / gap penalty | 0.35 / 0.20 |
| Per-video sequence limit | 4 |
| Result diversification frames/video | 1 |
| Minimum frame separation | 10000 ms or 300 frames |

Valid agent weights override heuristic and profile weights. Invalid or missing
agent values safely fall through to heuristic, then profile defaults.

---

## 8. Verification, limitations, and v3 recommendations

Existing tests cover fallback availability, QA fact routing toward text, TRAKE
action routing toward visual, explicit OCR gating, E1…En parsing, Vietnamese
temporal connectors, temporal-plan repair, and anchor/context result payloads.
The retrieval suite also validates batched semantic embedding of query variants.

Known limitations:

- Subagents do not execute independently in the active direct mode.
- Cache and OpenAI rate gate are process-local; cache has no LRU size bound.
- The 5-second OpenAI gate may add queue latency under concurrency.
- JSON recovery is robust but does not yet use provider-native structured output.
- Temporal conversion assumes 30 FPS rather than video-specific FPS.
- Without a reachable LLM, novel Vietnamese visual phrases can have weaker
  English semantic retrieval, though Vietnamese lexical retrieval remains.

Recommended v3 work:

1. Adopt provider-native structured outputs with a versioned schema.
2. Move cache and rate limiting to Redis with metrics and size limits.
3. Trace prompt version, latency, token usage, repair rate, and plan quality.
4. Use actual video FPS for temporal windows.
5. A/B evaluate direct versus deep_agent before enabling orchestration.
6. Maintain a Vietnamese regression corpus for visual, ASR, OCR, and temporal
   multi-event queries.

## 9. Conclusion

Agent v2 provides an inspectable conversion from user language to retrieval
operations: English semantic views for visual encoders, Vietnamese lexical
evidence for text indices, dynamic evidence weights, independent temporal
events, and deterministic recovery whenever the LLM cannot produce a usable
plan.

