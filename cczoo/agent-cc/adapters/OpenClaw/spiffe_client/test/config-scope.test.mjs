import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

import {configuredApiKey, requireConfiguredScope} from '../lib/config-scope.mjs';

const REDACTED = '__OPENCLAW_REDACTED__';

function config(apiKey) {
  const directory = mkdtempSync(join(tmpdir(), 'argus-config-scope-'));
  const path = join(directory, 'openclaw.json');
  writeFileSync(path, JSON.stringify({plugins:{entries:{openviking:{config:{apiKey}}}}}));
  return {directory, path};
}

test('resolves a redacted key from the effective Gateway config source', t => {
  const fixture = config('${OPENVIKING_API_KEY}');
  t.after(() => rmSync(fixture.directory, {recursive:true, force:true}));
  const key = configuredApiKey(REDACTED, fixture.path, {OPENVIKING_API_KEY:'configured-key'});
  requireConfiguredScope(
    {key, accountId:'shared', userId:'alice'},
    {key:'configured-key', accountId:'shared', userId:'alice'},
  );
});

test('rejects real mismatch, missing credentials, and wrong user', t => {
  const literal = config('configured-key');
  const variable = config('${OPENVIKING_API_KEY}');
  t.after(() => {
    rmSync(literal.directory, {recursive:true, force:true});
    rmSync(variable.directory, {recursive:true, force:true});
  });
  assert.throws(() => requireConfiguredScope(
    {key:configuredApiKey(REDACTED, literal.path, {}), accountId:'shared', userId:'alice'},
    {key:'other-key', accountId:'shared', userId:'alice'},
  ), /API_KEY_SCOPE_MISMATCH/);
  assert.throws(() => configuredApiKey(REDACTED, variable.path, {}), /API_KEY_SCOPE_MISMATCH/);
  assert.throws(() => requireConfiguredScope(
    {key:'configured-key', accountId:'shared', userId:'bob'},
    {key:'configured-key', accountId:'shared', userId:'alice'},
  ), /API_KEY_SCOPE_MISMATCH/);
});

test('never accepts the redaction sentinel as the configured key', t => {
  const fixture = config(REDACTED);
  t.after(() => rmSync(fixture.directory, {recursive:true, force:true}));
  assert.throws(() => configuredApiKey(REDACTED, fixture.path, {}), /API_KEY_SCOPE_MISMATCH/);
});
