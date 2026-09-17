import {test} from 'node:test';
import assert from 'node:assert/strict';
import {requireValidExport} from '../src/api/submissionValidation.ts';

test('invalid report blocks download even if a URI was returned', () => {
  assert.throws(()=>requireValidExport({valid:false,errors:['QA answer required']}, '/old.csv'), /QA answer required/);
});
test('valid report must also provide an artifact', () => {
  assert.throws(()=>requireValidExport({valid:true,errors:[]}, null), /did not produce/);
  assert.doesNotThrow(()=>requireValidExport({valid:true,errors:[]}, '/valid.csv'));
});
