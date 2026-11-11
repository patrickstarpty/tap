import assert from 'node:assert/strict';
import test from 'node:test';
import {createDocumentProvenance} from '../document-provenance.mjs';

function fixture() {
  let document = {}, unreadable = false, detached = false;
  const frame = {url:()=>'https://fixture.test/', isDetached:()=>detached,
    evaluateHandle:async () => {
      if (unreadable) throw new Error('unreadable');
      const captured = document;
      return {evaluate:async () => {if (unreadable) throw new Error('unreadable'); return captured === document;}, dispose:async()=>{}};
    }};
  const page = {frames:()=>detached ? [] : [frame]};
  return {page, frame, registry:createDocumentProvenance(page), replace:()=>{document={};}, unreadable:()=>{unreadable=true;}, detach:()=>{detached=true;}};
}

test('unreadable and detached documents cannot issue navigation proof', async () => {
  const f = fixture();
  const [first] = await f.registry.snapshot();
  assert.ok(first.frame_id && first.document_id);
  assert.deepEqual(await f.registry.snapshot(), [first]);
  f.replace();
  const [next] = await f.registry.snapshot();
  assert.equal(next.frame_id, first.frame_id);
  assert.notEqual(next.document_id, first.document_id);
  f.unreadable();
  assert.deepEqual(await f.registry.snapshot(), []);
  f.detach();
  assert.deepEqual(await f.registry.snapshot(), []);
});

test('oversized frame lists and URLs fail closed', async () => {
  const f = fixture();
  f.page.frames = () => Array(101).fill(f.frame);
  assert.deepEqual(await f.registry.snapshot(), []);
  f.page.frames = () => [f.frame];
  f.frame.url = () => 'https://fixture.test/' + 'x'.repeat(2048);
  assert.deepEqual(await f.registry.snapshot(), []);
});

test('unknown old-handle errors never mint replacement document IDs', async () => {
  let failOld = false, captures = 0;
  const frame = {url:()=>'https://fixture.test/', isDetached:()=>false,
    evaluateHandle:async () => {
      captures++;
      return {evaluate:async () => {if (failOld) throw new Error('unreadable transport'); return true;}, dispose:async()=>{}};
    }};
  const registry = createDocumentProvenance({frames:()=>[frame]});
  assert.equal((await registry.snapshot()).length, 1);
  failOld = true;
  assert.deepEqual(await registry.snapshot(), []);
  assert.equal(captures, 1);
});
