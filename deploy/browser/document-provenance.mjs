import { randomUUID } from 'node:crypto';

// Identity comes from actual Frame and Document objects, never URLs or events.
export function createDocumentProvenance(page) {
  const records = new WeakMap();
  function frames() {
    const current = page.frames();
    return current.length <= 100 ? current : [];
  }
  async function capture(frame) {
    if (!frame || !frames().includes(frame) || frame.isDetached()) return null;
    const url = frame.url();
    if (typeof url !== 'string' || !url || url.length > 2048) return null;
    let record = records.get(frame);
    if (!record) {
      record = {frame_id: randomUUID(), document_id: null, handle: null};
      records.set(frame, record);
    }
    let same = false;
    if (record.handle) {
      try { same = await record.handle.evaluate(doc => doc === document); } catch (error) {
        // A transient/unknown read failure is not evidence of document replacement.
        if (!/Execution context was destroyed|Cannot find context with specified id/.test(String(error?.message))) return null;
      }
    }
    if (!same) {
      let handle;
      try {
        handle = await frame.evaluateHandle(() => document);
        if (!await handle.evaluate(doc => doc === document)) throw new Error('document unavailable');
      } catch {
        await handle?.dispose().catch(() => {});
        return null;
      }
      await record.handle?.dispose().catch(() => {});
      record.handle = handle;
      record.document_id = randomUUID();
    }
    if (frame.isDetached() || !frames().includes(frame) || frame.url() !== url) return null;
    return {frame_id:record.frame_id, document_id:record.document_id, url};
  }
  async function snapshot() {
    try {
      const result = [];
      for (const frame of frames()) {
        const proof = await capture(frame).catch(() => null);
        if (proof) result.push(proof);
      }
      return result;
    } catch { return []; }
  }
  async function read(action, readValue) {
    const element = await page.locator(action.selector).elementHandle({timeout:30000});
    if (!element) throw new Error('read document unavailable');
    try {
      const frame = await element.ownerFrame();
      const before = await capture(frame);
      if (!before) throw new Error('read document unavailable');
      const value = await readValue(page, action);
      const after = await capture(frame);
      const current = await page.locator(action.selector).elementHandle({timeout:30000});
      try {
        if (!after || before.document_id !== after.document_id || before.url !== after.url ||
            !current || await current.ownerFrame() !== frame ||
            !await element.evaluate(node => node.isConnected && node.ownerDocument === document)) {
          throw new Error('read document changed');
        }
      } finally { await current?.dispose(); }
      return {value, source_frame_id:before.frame_id, source_document_id:before.document_id, source_url:before.url};
    } finally { await element.dispose(); }
  }
  return {snapshot, read};
}
