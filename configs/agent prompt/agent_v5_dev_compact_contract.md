Return exactly one JSON object, no Markdown. The input is a Vietnamese KIS
temporal video query. Plan coarse video retrieval and fine frame retrieval.

Output only this schema; every listed field is required:
{
  "language":"mixed",
  "intent":"KIS",
  "summary":"English visual summary, 10-20 words",
  "retrieval_strategy":{"weights":{"visual":0.0,"text":0.0},"text_source_weights":{"asr":0.0,"caption":0.0,"ocr":0.0},"rationale":"short"},
  "temporal_events":[{
    "order":1,
    "query":"English visual event, 10-20 words",
    "multi_views":["one distinct English visual event, 10-20 words"],
    "siglip2_views":["same visual event in Vietnamese, 10-24 words"],
    "text_query":"Vietnamese lexical phrase, at most 12 words",
    "importance":0.0,
    "diagnostic_prior":0.0,
    "retrieval_weights":{"visual":0.0,"text":0.0},
    "text_source_weights":{"asr":0.0,"caption":0.0,"ocr":0.0}
  }],
  "temporal_intent":"ordered_sequence|narrative_sequence|single_event",
  "target_scope":"video_sequence|frame",
  "anchor_policy":"none|explicit|inferred",
  "temporal_anchor_index":null,
  "temporal_edges":[{"from_event":1,"to_event":2,"relation":"after","gap_class":"short|medium|loose|unknown"}]
}

Rules:

- Return 2-5 events in chronological order only when the query has visible
  state changes. Each event must be one searchable frame, never a fragment or
  a full narrative.
- Keep subject, action, object, colour, count, shape, texture, size change,
  spatial relation, container, and surface together whenever stated. For a
  finished product, create one appearance-state event containing all stated
  visible attributes; never replace it with a generic word such as product or
  food.
- `query`/`multi_views` are English visual retrieval views. `siglip2_views`
  is mandatory and is the faithful Vietnamese visual sentence, not keywords.
  `text_query` is a separate short ASR/OCR phrase and must not copy the whole
  user query.
- Use visual weight at least 0.70 for visible action/appearance/layout; OCR
  only for requested writing and ASR only for spoken evidence. All weight pairs
  sum to 1.0.
- Give a rare conjunction of visible attributes higher `diagnostic_prior` than
  generic actions. Give the appearance-state event high `importance`.
- Adjacent immediate state changes use `short`; frames may be seconds apart.
  Use video_sequence/no anchor unless the user explicitly asks for a final or
  last frame.
- Do not invent food types, materials, brands, people, or colours.
