import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ReadCache,consumeCached} from '../src/api/mediaCache.ts';
test('coalesces concurrent reads, evicts failed requests and bounds capacity',async()=>{
 const cache=new ReadCache(1);let calls=0;
 const load=async()=>++calls;
 assert.deepEqual(await Promise.all([cache.get('a',load),cache.get('a',load)]),[1,1]);
 await cache.get('b',load);await cache.get('a',load);assert.equal(calls,3);
 await assert.rejects(cache.get('failed',async()=>{throw new Error('offline');}));
 assert.equal(await cache.get('failed',load),4);
 cache.clear();assert.equal(await cache.get('failed',load),5);
});
test('cancelled reader does not poison a shared request or another reader',async()=>{
 const cache=new ReadCache();let finish;let loads=0;
 const load=()=>{loads++;return new Promise(resolve=>{finish=resolve;});};
 const controller=new AbortController();
 const cancelled=consumeCached(cache.get('frame',load),controller.signal);
 const kept=consumeCached(cache.get('frame',load));
 controller.abort();await assert.rejects(cancelled,{name:'AbortError'});
 finish({frame:1});assert.deepEqual(await kept,{frame:1});
 assert.deepEqual(await cache.get('frame',load),{frame:1});assert.equal(loads,1);
});
