"""Compiled bundles must preserve document identity and safe associated readers."""

import subprocess

from tap.modules.workflows.application.browser_replay import REPLAY_HELPERS


def test_replay_proof_state_machine_fails_closed_without_document_evidence():
    source = REPLAY_HELPERS + r"""
const assert = require('node:assert/strict');
const contract = {fields:[{id:'age',readback_value:'30',attribute:'value',target_texts:['Age']}]};
const action = {type:'read',name:'age',equals:'30',attribute:'value',target_texts:['Age']};
const doc = {frame_id:'frame',document_id:'doc',url:'https://fixture.test/form'};
const read = {value:'30',source_frame_id:'frame',source_document_id:'doc',source_url:doc.url};
const proofs = new Map();
assert.throws(()=>recordAcceptance(contract,proofs,action,[],[],read),/mismatch/);
recordAcceptance(contract,proofs,action,[doc],[doc],read);
assertAcceptance(contract,proofs,[doc]);
recordAcceptance(contract,proofs,{type:'click'},[doc],[doc],null);
assert.throws(()=>assertAcceptance(contract,proofs,[doc]),/missing or stale/);
// A final check alone cannot turn pending evidence into a successful read-only observation.
const next={...doc,document_id:'next'};
assert.throws(()=>assertAcceptance(contract,proofs,[next]),/missing or stale/);
recordAcceptance(contract,proofs,{type:'snapshot'},[doc],[next],null);
assertAcceptance(contract,proofs,[next]);
recordAcceptance(contract,proofs,{type:'snapshot'},[next],[],null);
assert.throws(()=>assertAcceptance(contract,proofs,[]),/missing or stale/);
assert.equal(proofs.size,0);
// Legacy bundles have no invented reviewed contract.
assertAcceptance(null,proofs,[]);
"""
    subprocess.run(["node", "-e", source], check=True, capture_output=True, text=True)
