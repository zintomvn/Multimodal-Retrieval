# Backend Documentation Index

This directory contains backend architecture notes, runbooks, data contracts, and implementation reports for the multimodal retrieval system.

## Recommended Reading Order

1. [Media Delivery Technical Report](./media-delivery-technical-report.md)
2. [Model Pipeline Guide](./guides/model_pipeline.md)
3. [Cloud E2E Runbook](./runbooks/cloud_e2e.md)
4. [Database ERD](./specs/database_erd.md)
5. [Database Schema Demo v1](./specs/database_schema_demo_v1.md)

## Sections

| Folder | Purpose |
| --- | --- |
| [`guides/`](./guides/) | Implementation guides for model runtime, adapters, pipeline behavior, and integration decisions. |
| [`runbooks/`](./runbooks/) | Operational checklists for local/cloud runs, smoke tests, and incident-style troubleshooting. |
| [`specs/`](./specs/) | Data model, database schema, and ERD documentation. |
| [`milestones/`](./milestones/) | Planning, milestone reports, and execution notes. |

## Current Media-Related Documents

| Document | Scope |
| --- | --- |
| [Media Delivery Technical Report](./media-delivery-technical-report.md) | Backend frame/video serving architecture, sequence diagrams, GCS/local fallback order, API contracts, caching, and troubleshooting. |
| [Cloud E2E Runbook](./runbooks/cloud_e2e.md) | End-to-end cloud checklist across storage, database, retrieval, and indexing services. |
| [Database ERD](./specs/database_erd.md) | Tables and relationships used by media, retrieval, and submission flows. |
