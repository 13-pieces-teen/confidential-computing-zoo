import fs from 'node:fs';

const REDACTED = '__OPENCLAW_REDACTED__';

function resolveEnvironment(value, env) {
  if (typeof value !== 'string' || !value) throw new Error('API_KEY_SCOPE_MISMATCH');
  return value.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_match, name) => {
    const replacement = env[name];
    if (typeof replacement !== 'string' || !replacement) throw new Error('API_KEY_SCOPE_MISMATCH');
    return replacement;
  });
}

export function configuredApiKey(reported, configPath, env = process.env) {
  if (reported !== REDACTED) return reported;
  if (!configPath) throw new Error('API_KEY_SCOPE_MISMATCH');
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  } catch {
    throw new Error('API_KEY_SCOPE_MISMATCH');
  }
  const configured = raw?.plugins?.entries?.openviking?.config?.apiKey;
  const resolved = resolveEnvironment(configured, env);
  if (!resolved || resolved === REDACTED) throw new Error('API_KEY_SCOPE_MISMATCH');
  return resolved;
}

export function requireConfiguredKey(actual, expected) {
  if (!actual || actual !== expected) throw new Error('API_KEY_SCOPE_MISMATCH');
}

export function requireConfiguredScope(actual, expected) {
  requireConfiguredKey(actual.key, expected.key);
  if (actual.accountId !== expected.accountId || actual.userId !== expected.userId) {
    throw new Error('API_KEY_SCOPE_MISMATCH');
  }
}
