import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readWorkspace,saveWorkspace,newQueryName} from '../src/workspace.ts';
const draft={queryType:'KIS',queryName:'query-1-kis',queryText:'custom draft',exportFileName:'answer',videoCodeQuery:'L01_V001',videoFrameQuery:'100',topK:10,useExpansion:false,useAgentPlanning:false,useMetadata:true,kisTemporalMode:false,temporalStrategy:'vortex_k_context',visualSearchMode:'openclip',reasoningModel:'gpt-4o',sourceMode:'ocr',temporalEvents:[]};
test('workspace round trip preserves options and independent task drafts',()=>{
  let raw=null;const storage={getItem:()=>raw,setItem:(_,v)=>raw=v};
  const value={version:1,datasetId:'real',active:draft,drafts:{QA:{...draft,queryType:'QA',queryText:'other'}},selected:[]};
  assert.equal(saveWorkspace(value,storage),null);
  assert.deepEqual(readWorkspace(storage).value,value);
  assert.notEqual(newQueryName('KIS'),newQueryName('KIS'));
});
test('invalid saves and quota failures are visible without clearing storage',()=>{
  assert.ok(readWorkspace({getItem:()=>'{broken'}).error);
  assert.ok(readWorkspace({getItem:()=>JSON.stringify({version:2})}).error);
  assert.ok(saveWorkspace({}, {setItem:()=>{throw new Error('quota');}}));
});
