import {test} from 'node:test';
import assert from 'node:assert/strict';
import {LatestRequest} from '../src/api/latestRequest.ts';

test('double submit cannot start concurrent work', () => {
  const gate = new LatestRequest();
  const first = gate.begin();
  assert.ok(first);
  assert.equal(gate.begin(), null);
  assert.equal(gate.finish(first), true);
  assert.ok(gate.begin());
});

test('late response and finally from A cannot overwrite or finish B', () => {
  const gate = new LatestRequest();
  const first = gate.begin();
  gate.cancel();
  const second = gate.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(gate.isCurrent(first), false);
  assert.equal(gate.finish(first), false);
  assert.equal(gate.isCurrent(second), true);
  assert.equal(gate.begin(), null);
  assert.equal(gate.finish(second), true);
});
