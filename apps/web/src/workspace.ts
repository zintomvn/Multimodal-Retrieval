import type { QueryType, SearchResult, SubmissionRow, VisualSearchMode } from './types';

export type SearchTask = QueryType | 'VIDEO';
export type SourceMode = 'auto' | 'ocr' | 'asr' | 'scene';
export interface SearchDraft {
  queryType: SearchTask;
  queryName: string;
  queryText: string;
  exportFileName: string;
  qaAnswer?: string;
  videoCodeQuery: string;
  videoFrameQuery: string;
  topK: number;
  useExpansion: boolean;
  useAgentPlanning: boolean;
  useMetadata: boolean;
  kisTemporalMode: boolean;
  temporalStrategy: 'vortex_k_context' | 'aithena_weighted_ats' | 'dev_first_search';
  visualSearchMode: VisualSearchMode;
  reasoningModel: 'gpt-4o' | 'gpt-5-nano' | 'gpt-5.6-luna';
  sourceMode: SourceMode;
  temporalEvents: string[];
  videoFilter?: string;
  timeStart?: string;
  timeEnd?: string;
}
export interface StoredWorkspace {
  version: 1;
  mode?: 'Search' | 'Auto' | 'Chat';
  datasetId: string;
  active: SearchDraft;
  drafts: Partial<Record<SearchTask, SearchDraft>>;
  selected: SubmissionRow[];
  history?: StoredHistory[];
  trakeChoices?: Array<{eventIndex:number;result:SearchResult;frame:SearchResult['sequence_frames'][number]}>;
  scrollTop?: number;
}
export interface StoredHistory {
  id: string; mode: 'Search' | 'Auto' | 'Chat'; queryType: SearchTask;
  queryName: string; queryText: string; resultCount: number; createdAt: string;
  results: SearchResult[]; trace: Array<{title:string;detail:string;status:'done'|'running'|'queued'|'warning';raw?:unknown}>;
  datasetId?: string; draft?: SearchDraft;
}
export const WORKSPACE_KEY = 'multimodal-workspace-v1';
export const newQueryName = (type: SearchTask) => `query-${crypto.randomUUID()}-${type === 'VIDEO' ? 'kis' : type.toLowerCase()}`;

export function validDraft(value: unknown): value is SearchDraft {
  if (!value || typeof value !== 'object') return false;
  const d = value as SearchDraft;
  return ['KIS','QA','TRAKE','VIDEO'].includes(d.queryType)
    && ['queryName','queryText','exportFileName','videoCodeQuery','videoFrameQuery'].every(k=>typeof (d as unknown as Record<string,unknown>)[k]==='string')
    && Number.isInteger(d.topK) && d.topK>=1 && d.topK<=100
    && ['useExpansion','useAgentPlanning','useMetadata','kisTemporalMode'].every(k=>typeof (d as unknown as Record<string,unknown>)[k]==='boolean')
    && ['auto','ocr','asr','scene'].includes(d.sourceMode)
    && ['openclip','siglip2','both'].includes(d.visualSearchMode)
    && ['gpt-4o','gpt-5-nano','gpt-5.6-luna'].includes(d.reasoningModel)
    && ['vortex_k_context','aithena_weighted_ats','dev_first_search'].includes(d.temporalStrategy)
    && Array.isArray(d.temporalEvents) && d.temporalEvents.length<=8 && d.temporalEvents.every(e=>typeof e==='string');
}
export function readWorkspace(storage: Pick<Storage,'getItem'> = localStorage): {value:StoredWorkspace|null;error:string|null} {
  try {
    const raw=storage.getItem(WORKSPACE_KEY);
    if (!raw) return {value:null,error:null};
    const value=JSON.parse(raw) as StoredWorkspace;
    if (value.version!==1 || typeof value.datasetId!=='string' || !validDraft(value.active)
      || (value.mode !== undefined && !['Search','Auto','Chat'].includes(value.mode))
      || (value.trakeChoices !== undefined && (!Array.isArray(value.trakeChoices) || !value.trakeChoices.every(c=>c && Number.isInteger(c.eventIndex) && c.eventIndex>=1 && c.eventIndex<=8 && c.frame && Number.isInteger(c.frame.frame_idx) && c.result && typeof c.result.id==='string' && Array.isArray(c.result.sequence_frames))))
      || (value.history !== undefined && (!Array.isArray(value.history) || !value.history.every(h=>h && typeof h.id==='string'
        && typeof h.queryText==='string' && typeof h.queryName==='string' && typeof h.createdAt==='string'
        && ['Search','Auto','Chat'].includes(h.mode) && ['KIS','QA','TRAKE','VIDEO'].includes(h.queryType)
        && Array.isArray(h.results) && h.results.every(r=>r && typeof r.id==='string' && typeof r.video_code==='string' && Array.isArray(r.sequence_frames) && r.score_breakdown)
        && Array.isArray(h.trace) && h.trace.every(t=>t && typeof t.title==='string' && typeof t.detail==='string')
        && (!h.draft || validDraft(h.draft)))))
      || !value.drafts || !Object.values(value.drafts).every(validDraft)
      || !Array.isArray(value.selected) || !value.selected.every(r=>r && typeof r.query_name==='string' && typeof r.video_code==='string'
        && ['KIS','QA','TRAKE'].includes(r.query_type) && Number.isInteger(r.rank) && r.rank>0
        && (r.answer===null || typeof r.answer==='string') && Array.isArray(r.frame_indices) && r.frame_indices.every(n=>Number.isInteger(n)&&n>=0))) {
      return {value:null,error:'Saved workspace is incompatible or damaged. It has been kept for recovery.'};
    }
    return {value,error:null};
  } catch { return {value:null,error:'Cannot read saved workspace. Existing storage has not been cleared.'}; }
}
export function saveWorkspace(value: StoredWorkspace, storage: Pick<Storage,'setItem'> = localStorage): string | null {
  try { storage.setItem(WORKSPACE_KEY,JSON.stringify(value)); return null; }
  catch { return 'Autosave failed. Your current work remains open; check browser storage before reloading.'; }
}
