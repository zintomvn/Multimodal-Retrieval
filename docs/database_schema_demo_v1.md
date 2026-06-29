# Database Schema v1 (Demo-Aligned)

Tai lieu nay chot schema PostgreSQL de backend ingest va retrieval bám dung du lieu trong thu muc `demo/`.

Muc tieu:
- dong bo 1-1 voi cac file `per_video_summary.csv`, `shot_segments.csv`, `annotations.jsonl`, `Event Embedding/event_mapping.csv`;
- tao khoa dinh danh on dinh de map qua Elasticsearch va Milvus;
- de mo rong len production ma khong pha vo contract import.

## 1. Nguyen tac thiet ke

- Dung natural business ID lam PK cho media entities:
  - `video_id` = `L30_V001`
  - `shot_id` = `L30_V001_S0000`
  - `keyframe_id` = `L30_V001_F000037`
  - `event_id` = `L30_V001_E000000`
- Tat ca ten bang/cot lowercase.
- Postgres la source-of-truth cho metadata va quan he.
- Elasticsearch chi giu text index.
- Milvus chi giu vector + id lien ket.
- Truong du lieu goc de debug/rebuild duoc luu dang `jsonb` khi can.

## 2. Inventory demo (snapshot de doi chieu import)

Tu du lieu trong `demo/`:
- `per_video_summary.csv`: 96 videos.
- `shot_segments.csv`: 13278 rows keyframe.
- `annotations.jsonl`: 318 rows.
- `Event Embedding/event_mapping.csv`: 2737 rows event.

Luu y: can co buoc reconcile media truoc import vi co kha nang chenhlech giua metadata va file JPG thuc te.

## 3. Canonical schema (core ingestion)

```sql
-- Required for UUID default
create extension if not exists pgcrypto;

create table if not exists datasets (
  dataset_id uuid primary key default gen_random_uuid(),
  dataset_code text not null unique,               -- ex: l30
  name text not null,
  version text not null,                           -- ex: v1
  root_uri text not null,                          -- ex: /app/demo
  status text not null default 'ready',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists videos (
  video_id text primary key,                       -- ex: L30_V001
  dataset_id uuid not null references datasets(dataset_id) on delete cascade,
  video_name text not null,                        -- ex: L30_V001.mp4
  duration_seconds double precision not null check (duration_seconds >= 0),
  fps double precision check (fps > 0),
  num_keyframes integer check (num_keyframes >= 0),
  embedding_shape text,                            -- ex: [165, 512]
  source_feature_path text,
  source_map_path text,
  source_video_path text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists shots (
  shot_id text primary key,                        -- ex: L30_V001_S0000
  video_id text not null references videos(video_id) on delete cascade,
  shot_index integer not null check (shot_index >= 0),
  start_frame integer not null check (start_frame >= 0),
  end_frame integer not null check (end_frame >= start_frame),
  start_seconds double precision not null check (start_seconds >= 0),
  end_seconds double precision not null check (end_seconds >= start_seconds),
  boundary_threshold double precision,
  created_at timestamptz not null default now(),
  unique (video_id, shot_index)
);

create table if not exists keyframes (
  keyframe_id text primary key,                    -- ex: L30_V001_F000037
  video_id text not null references videos(video_id) on delete cascade,
  shot_id text not null references shots(shot_id) on delete cascade,
  frame_idx integer not null check (frame_idx >= 0),
  frame_seconds double precision not null check (frame_seconds >= 0),
  frame_type text not null check (frame_type in ('first', 'middle', 'last')),
  map_n integer,                                   -- 1-based from map-keyframes CSV
  embedding_index_0 integer check (embedding_index_0 >= 0), -- map_n - 1
  image_rel_path text not null,                    -- ex: L30_V001/shot_0000_first_f000000.jpg
  image_storage_key text,                          -- object storage key
  image_url text,                                  -- presigned/public URL
  is_media_present boolean not null default true,
  created_at timestamptz not null default now(),
  unique (video_id, frame_idx)
);

create table if not exists frame_annotations (
  keyframe_id text primary key references keyframes(keyframe_id) on delete cascade,
  caption text,
  ocr_texts jsonb not null default '[]'::jsonb,
  detected_objects jsonb not null default '[]'::jsonb,
  object_counts jsonb not null default '{}'::jsonb,
  detections jsonb not null default '[]'::jsonb,
  source_image_name text,
  source_image_path text,
  annotation_version text,
  updated_at timestamptz not null default now()
);

create table if not exists events (
  event_id text primary key,                       -- ex: L30_V001_E000000
  video_id text not null references videos(video_id) on delete cascade,
  embedding_index_0 integer not null check (embedding_index_0 >= 0),
  start_seconds double precision not null check (start_seconds >= 0),
  end_seconds double precision not null check (end_seconds >= start_seconds),
  start_frame integer not null check (start_frame >= 0),
  end_frame integer not null check (end_frame >= start_frame),
  representative_keyframe_id text references keyframes(keyframe_id),
  n_shots integer check (n_shots >= 0),
  n_keyframes integer check (n_keyframes >= 0),
  shot_ids_raw text,
  keyframe_embedding_indices_raw text,
  created_at timestamptz not null default now(),
  unique (video_id, embedding_index_0)
);

create table if not exists event_keyframes (
  event_id text not null references events(event_id) on delete cascade,
  seq_no integer not null check (seq_no >= 0),
  keyframe_id text not null references keyframes(keyframe_id) on delete cascade,
  keyframe_embedding_index_0 integer check (keyframe_embedding_index_0 >= 0),
  primary key (event_id, seq_no),
  unique (event_id, keyframe_id)
);
```

## 4. Indexes bat buoc (Postgres)

```sql
create index if not exists idx_videos_dataset on videos(dataset_id);
create index if not exists idx_shots_video_time on shots(video_id, start_seconds, end_seconds);
create index if not exists idx_keyframes_video_time on keyframes(video_id, frame_seconds);
create index if not exists idx_keyframes_shot on keyframes(shot_id, frame_idx);
create index if not exists idx_events_video_time on events(video_id, start_seconds, end_seconds);
create index if not exists idx_event_keyframes_keyframe on event_keyframes(keyframe_id);

-- jsonb query support
create index if not exists idx_ann_objects_gin on frame_annotations using gin (detected_objects);
create index if not exists idx_ann_ocr_gin on frame_annotations using gin (ocr_texts);
```

## 5. Mapping sang Elasticsearch va Milvus

- Elasticsearch index `keyframe_annotations`:
  - id = `keyframe_id`
  - fields: `video_id`, `shot_id`, `frame_seconds`, `caption`, `ocr_texts`, `detected_objects`
- Milvus collection `keyframe_embeddings`:
  - scalar id: `keyframe_id`
  - vector dim: 512
- Milvus collection `event_embeddings`:
  - scalar id: `event_id`
  - vector dim: 512

## 6. DQ checks sau moi lan import

```sql
-- 1) keyframes count
select count(*) as keyframes_count from keyframes;

-- 2) events count
select count(*) as events_count from events;

-- 3) orphan checks
select count(*) as orphan_keyframes
from keyframes k
left join shots s on s.shot_id = k.shot_id
where s.shot_id is null;

select count(*) as orphan_annotations
from frame_annotations a
left join keyframes k on k.keyframe_id = a.keyframe_id
where k.keyframe_id is null;

-- 4) duplicate frame index per video (must be 0)
select video_id, frame_idx, count(*)
from keyframes
group by video_id, frame_idx
having count(*) > 1;
```

## 7. Supabase security note

- Neu bang dat trong schema duoc expose qua Data API (`public`), bat buoc bat RLS va policy ro rang.
- Neu backend la trusted service va khong expose Data API truc tiep cho client, van nen:
  - khong cap quyen rong cho `anon`/`authenticated`;
  - tach key server/client ro rang;
  - review lai policy truoc khi mo REST public.

## 8. Contract status

Tai lieu nay la schema contract uu tien de implement Module 1 va Module 2 trong `docs/tasks/backend_milestones/`.

