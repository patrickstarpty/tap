import { pathToFileURL } from 'node:url';
import { mailboxRequest } from './model-shim.mjs';

const fields = {
  plan: new Set(['type', 'steps', 'reason']),
  navigate: new Set(['type', 'url']),
  snapshot: new Set(['type', 'selector']),
  click: new Set(['type', 'selector']),
  fill: new Set(['type', 'selector', 'value']),
  select: new Set(['type', 'selector', 'value']),
  press: new Set(['type', 'selector', 'key']),
  wait: new Set(['type', 'selector']),
  screenshot: new Set(['type']),
  read: new Set(['type','selector','name','attribute','equals','contains','nonempty','positive','pattern','timeout_ms']),
};

export function validateAction(action) {
  if (!action || typeof action !== 'object' || Array.isArray(action) || !Object.hasOwn(fields, action.type)) {
    throw new Error('unsupported browser action');
  }
  const guarded = ['click','fill','select','press','wait','read'].includes(action.type);
  if (guarded && action.target_texts !== undefined && (!Array.isArray(action.target_texts) || action.target_texts.length < 1 || action.target_texts.length > 12 || action.target_texts.some(value => typeof value !== 'string' || !value.trim() || value.length > 200))) throw new Error('invalid target constraints');
  if (guarded && action.scope_text !== undefined && (typeof action.scope_text !== 'string' || !action.scope_text.trim() || action.scope_text.length > 200)) throw new Error('invalid scope constraint');
  if (Object.keys(action).some(key => key !== 'step' && !(guarded && ['target_texts','scope_text'].includes(key)) && !fields[action.type].has(key))) throw new Error('unsupported browser action fields');
  if (action.step !== undefined && (typeof action.step !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(action.step))) throw new Error('invalid browser step');
  if (action.type === 'plan') {
    if (!Array.isArray(action.steps) || action.steps.length < 1 || action.steps.length > 24) throw new Error('invalid BDD plan');
    for (const [index, step] of action.steps.entries()) {
      if (!step || typeof step !== 'object' || Object.keys(step).some(key => !['id','keyword','title','expected_result','verification_kind'].includes(key)) ||
          typeof step.id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(step.id) ||
          !['Given','When','Then','And'].includes(step.keyword) || typeof step.title !== 'string' || !step.title.trim() || step.title.length > 200 ||
          (step.expected_result !== undefined && (typeof step.expected_result !== 'string' || step.expected_result.length > 400))) throw new Error('invalid BDD plan step');
      if (step.verification_kind !== undefined) {
        const label = `BDD plan step ${index + 1} (id ${step.id})`;
        if (!['input','result'].includes(step.verification_kind)) throw new Error(`${label}: verification_kind must be 'input' or 'result'`);
        if (step.keyword !== 'Then') throw new Error(`${label}: verification_kind is only allowed on Then steps; change keyword to Then or remove verification_kind`);
      }
    }
  }
  if (action.type === 'snapshot' && action.selector !== undefined && (typeof action.selector !== 'string' || !action.selector || action.selector.length > 1000)) throw new Error('invalid snapshot selector');
  if (action.type === 'navigate' && (typeof action.url !== 'string' || !/^https?:\/\/[^\s]+$/.test(action.url))) throw new Error('browser action requires absolute URL');
  if (['click', 'fill', 'select', 'press', 'wait', 'read'].includes(action.type) && (typeof action.selector !== 'string' || !action.selector || action.selector.length > 1000)) throw new Error('invalid browser selector');
  if (['fill', 'select'].includes(action.type) && (typeof action.value !== 'string' || action.value.length > 4000)) throw new Error('invalid browser value');
  if (action.type === 'press' && (typeof action.key !== 'string' || action.key.length > 100)) throw new Error('invalid browser key');
  if (action.type === 'read') {
    if (typeof action.name !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(action.name)) throw new Error('invalid output name');
    for (const [key, cap] of Object.entries({attribute:100, equals:4000, contains:4000, pattern:200})) {
      if (action[key] !== undefined && (typeof action[key] !== 'string' || action[key].length > cap)) throw new Error('invalid read condition');
    }
    if (['nonempty','positive'].some(key => action[key] !== undefined && typeof action[key] !== 'boolean')) throw new Error('invalid read condition');
    if (action.timeout_ms !== undefined && (!Number.isInteger(action.timeout_ms) || action.timeout_ms < 100 || action.timeout_ms > 30000)) throw new Error('invalid read timeout');
  }
  if (Buffer.byteLength(JSON.stringify(action)) > 16384) throw new Error('browser action exceeds size cap');
  return action;
}

export function boundedObservation(observation) {
  if (!observation || typeof observation !== 'object' || Array.isArray(observation)) throw new Error('invalid browser observation');
  const snapshot = String(observation.bounded_snapshot || '');
  const result = { ...observation };
  if (snapshot.length > 5000) {
    result.bounded_snapshot = snapshot.slice(0, 5000);
    result.truncated = true;
  }
  if (Buffer.byteLength(JSON.stringify(result)) > 12000) {
    result.fields = [];
    result.fields_truncated = true;
    for (const field of observation.fields || []) {
      const candidate = { ...field, options: field.options?.slice(0, 12) };
      result.fields.push(candidate);
      if (Buffer.byteLength(JSON.stringify(result)) > 12000) { result.fields.pop(); break; }
    }
  }
  if (Buffer.byteLength(JSON.stringify(result)) > 12000) throw new Error('browser observation exceeds helper cap');
  return result;
}

export async function readAction(args, input) {
  let raw;
  if (args.length === 1 && args[0] === '--stdin') {
    const chunks = [];
    let size = 0;
    for await (const chunk of input) {
      const bytes = Buffer.from(chunk);
      size += bytes.length;
      if (size > 16384) throw new Error('browser action exceeds size cap');
      chunks.push(bytes);
    }
    raw = Buffer.concat(chunks).toString('utf8');
  } else if (args.length === 1) {
    raw = args[0];
  } else {
    throw new Error('Use browserctl --stdin with a quoted heredoc for JSON actions');
  }
  return validateAction(JSON.parse(raw));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.env.TAP_CODING_MAILBOX !== '/mailbox') throw new Error('coding mailbox unavailable');
    const action = await readAction(process.argv.slice(2), process.stdin);
    const result = await mailboxRequest(process.env.TAP_CODING_MAILBOX, 'browser', action, 30000);
    // An action error still carries the observed DOM and disambiguating controls.
    // Preserve it as structured output so the agent can inspect and recover.
    process.stdout.write(JSON.stringify(boundedObservation(result)) + '\n');
  } catch (error) {
    process.stderr.write(String(error.message).slice(0, 300) + '\n');
    process.exitCode = 1;
  }
}
