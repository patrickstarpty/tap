"""Fixed current-document proof protocol, mirrored without cross-product imports."""

DOCUMENT_ACCEPTANCE_HELPER = r"""
// Identity comes from actual Frame and Document objects, never URLs or events.
function createDocumentProvenance(page) {
  const records = new WeakMap();
  let sequence = 0;
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
      record = {frame_id: String(++sequence), document_id: null, handle: null};
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
      record.document_id = String(++sequence);
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

function recordAcceptance(contract, proofs, action, before, after, read) {
  if (!contract) return;
  const previous = new Map(before.map(doc=>[doc.frame_id,doc.document_id]));
  const current = new Map(after.map(doc=>[doc.frame_id,doc.document_id]));
  const write = ['navigate','click','fill','select','press','scroll'].includes(action.type);
  for (const [name, proof] of [...proofs]) {
    const source = proof.source_document_id;
    const frame = proof.source_frame_id;
    if (write) {
      proofs.delete(name);
      if (!['active','checked_pending'].includes(proof.state) || previous.get(frame) !== source || !current.has(frame)) continue;
      if (['navigate','click'].includes(action.type) && current.get(frame) !== source) {
        proofs.set(name,{...proof,state:'archived',destination:current.get(frame)});
      } else if (action.type === 'click' && current.get(frame) === source) {
        proofs.set(name,{...proof,state:'pending'});
      }
    } else if (!current.has(frame)) {
      proofs.delete(name);
    } else if (proof.state === 'pending') {
      if (current.get(frame) !== source) proofs.delete(name);
    } else if (proof.state === 'checked_pending') {
      if (current.get(frame) !== source) proofs.set(name,{...proof,state:'archived',destination:current.get(frame)});
    } else if (current.get(frame) !== (proof.state === 'archived' ? proof.destination : source)) {
      proofs.delete(name);
    }
  }
  if (action.type !== 'read') return;
  const field = contract.fields.find(field=>field.id===action.name);
  if (!field) return;
  const previousProof = proofs.get(field.id);
  proofs.delete(field.id);
  if (action.equals!==field.readback_value || read?.value!==field.readback_value ||
      (action.attribute||'text')!==field.attribute ||
      !Array.isArray(action.target_texts) || !action.target_texts.length ||
      action.target_texts.some(text=>!field.target_texts.includes(text)) ||
      (action.scope_text||null)!==(field.scope_text||null) ||
      !read.source_frame_id || !read.source_document_id ||
      current.get(read.source_frame_id)!==read.source_document_id ||
      !after.some(doc=>doc.frame_id===read.source_frame_id && doc.url===read.source_url)) {
    throw new Error('reviewed input readback mismatch: '+field.id);
  }
  const read_action = Object.freeze({...action,target_texts:Object.freeze([...action.target_texts])});
  const sameClick = ['pending','checked_pending'].includes(previousProof?.state) && previousProof.source_frame_id===read.source_frame_id && previousProof.source_document_id===read.source_document_id;
  proofs.set(field.id,{...read,read_action,state:sameClick ? 'checked_pending' : 'active'});
}
async function recheckPendingAcceptance(page, contract, proofs, tracker) {
  if (!contract) return;
  for (const [name, proof] of [...proofs]) {
    if (proof.state !== 'pending') continue;
    // Remove old evidence first. A mismatch or navigation during the recheck
    // must never resurrect the pre-click value on a later document transition.
    proofs.delete(name);
    const action = proof.read_action;
    if (!action) continue;
    try {
      const current = await tracker.snapshot();
      if (!current.some(doc=>doc.frame_id===proof.source_frame_id && doc.document_id===proof.source_document_id)) continue;
      const read = await tracker.read(action, async () => {
        await guardTarget(page, action);
        return readValue(page, {...action,timeout_ms:100});
      });
      if (read.source_frame_id!==proof.source_frame_id || read.source_document_id!==proof.source_document_id || read.value!==action.equals) continue;
      proofs.set(name,{...read,read_action:action,state:'checked_pending'});
    } catch { /* Unknown/raced/mismatched reads cannot preserve pending proof. */ }
  }
}
function assertAcceptance(contract, proofs, documents) {
  const current = new Map(documents.map(doc=>[doc.frame_id,doc.document_id]));
  if (contract && contract.fields.some(field=>{
    const proof = proofs.get(field.id);
    return !proof || proof.state==='pending' || current.get(proof.source_frame_id)!==(proof.state==='archived' ? proof.destination : proof.source_document_id);
  })) {
    throw new Error('reviewed input proof missing or stale');
  }
}
"""


def current_field_proven(execution, field, step_id):
    """Validate the current generated attachment, never old AA proof status."""
    result = execution.get("result")
    if not isinstance(result, dict):
        return False
    outputs = result.get("outputs")
    if not isinstance(outputs, list):
        return False
    candidates = [
        item
        for item in outputs
        if isinstance(item, dict)
        and item.get("schema_version") == "lca-debug-result-v1"
    ]
    if len(candidates) != 1:
        return False
    evidence = candidates[0].get("requirements_evidence")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != {"version", "error", "documents", "fields"}
        or type(evidence["version"]) is not int
        or evidence["version"] != 1
        or evidence["error"] is not False
    ):
        return False
    documents, fields = evidence["documents"], evidence["fields"]
    if (
        not isinstance(documents, list)
        or len(documents) > 100
        or not isinstance(fields, dict)
        or len(fields) > 20
    ):
        return False
    ids = set()
    for document in documents:
        if (
            not isinstance(document, dict)
            or set(document) != {"frame_id", "document_id", "url"}
            or any(
                not isinstance(value, str) or not value or len(value) > 2048
                for value in document.values()
            )
            or document["frame_id"] in ids
        ):
            return False
        ids.add(document["frame_id"])
    proof = fields.get(field["id"])
    if not isinstance(proof, dict):
        return False
    keys = {
        "step_id",
        "value",
        "source_frame_id",
        "source_document_id",
        "source_url",
        "state",
    }
    if proof.get("state") == "archived":
        keys.add("destination")
    if (
        set(proof) != keys
        or proof.get("state") not in {"active", "checked_pending", "archived"}
        or proof.get("step_id") != step_id
        or proof.get("value") != field["readback_value"]
    ):
        return False
    if any(
        not isinstance(proof.get(key), str) or not proof[key] or len(proof[key]) > 2048
        for key in keys - {"value"}
    ):
        return False
    destination = (
        proof["destination"]
        if proof["state"] == "archived"
        else proof["source_document_id"]
    )
    if proof["state"] == "archived" and destination == proof["source_document_id"]:
        return False
    return any(
        document["frame_id"] == proof["source_frame_id"]
        and document["document_id"] == destination
        and (proof["state"] == "archived" or document["url"] == proof["source_url"])
        for document in documents
    )
