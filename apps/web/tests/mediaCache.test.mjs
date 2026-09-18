import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ReadCache} from '../src/api/mediaCache.ts';
test('coalesces concurrent reads, evicts failed requests and bounds capacity',async()=>{
 const cache=new ReadCache(1);let calls=0;
 const load=async()=>++calls;
 assert.deepEqual(await Promise.all([cache.get('a',load),cache.get('a',load)]),[1,1]);
 await cache.get('b',load);await cache.get('a',load);assert.equal(calls,3);
 await assert.rejects(cache.get('failed',async()=>{throw new Error('offline');}));
 assert.equal(await cache.get('failed',load),4);
 cache.clear();assert.equal(await cache.get('failed',load),5);
});
