import { pathToFileURL } from 'node:url';
import { mailboxRequest } from './model-shim.mjs';

const fields = {
  navigate: new Set(['type', 'url']),
  snapshot: new Set(['type']),
  click: new Set(['type', 'selector']),
  fill: new Set(['type', 'selector', 'value']),
  select: new Set(['type', 'selector', 'value']),
  press: new Set(['type', 'selector', 'key']),
  wait: new Set(['type', 'selector']),
  screenshot: new Set(['type']),
};

export function validateAction(action) {
  if (!action || typeof action !== 'object' || Array.isArray(action) || !Object.hasOwn(fields, action.type)) {
    throw new Error('unsupported browser action');
  }
  if (Object.keys(action).some(key => !fields[action.type].has(key))) throw new Error('unsupported browser action fields');
  if (action.type === 'navigate' && (typeof action.url !== 'string' || !/^https?:\/\/[^\s]+$/.test(action.url))) throw new Error('browser action requires absolute URL');
  if (['click', 'fill', 'select', 'press', 'wait'].includes(action.type) && (typeof action.selector !== 'string' || !action.selector || action.selector.length > 1000)) throw new Error('invalid browser selector');
  if (['fill', 'select'].includes(action.type) && (typeof action.value !== 'string' || action.value.length > 4000)) throw new Error('invalid browser value');
  if (action.type === 'press' && (typeof action.key !== 'string' || action.key.length > 100)) throw new Error('invalid browser key');
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
  }
  if (Buffer.byteLength(JSON.stringify(result)) > 12000) throw new Error('browser observation exceeds helper cap');
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv.length !== 3) throw new Error('browserctl requires one JSON action argument');
    if (process.env.TAP_CODING_MAILBOX !== '/mailbox') throw new Error('coding mailbox unavailable');
    const action = validateAction(JSON.parse(process.argv[2]));
    const result = await mailboxRequest(process.env.TAP_CODING_MAILBOX, 'browser', action, 30000);
    if (result.error) throw new Error(String(result.error).slice(0, 300));
    process.stdout.write(JSON.stringify(boundedObservation(result)) + '\n');
  } catch (error) {
    process.stderr.write(String(error.message).slice(0, 300) + '\n');
    process.exitCode = 1;
  }
}
