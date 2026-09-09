#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_ROOT="$ROOT/../../../core/spire/helpers/spiffe-helper"
temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
if [[ -n "${ARGUS_TEST_UPSTREAM:-}" ]]; then
    cp "$ARGUS_TEST_UPSTREAM" "$temporary/upstream.tgz"
else
    python3 - "$ROOT/upstream.lock.json" "$temporary/upstream.tgz" <<'PY'
import json, pathlib, sys, urllib.request
with urllib.request.urlopen(json.loads(pathlib.Path(sys.argv[1]).read_text())['url'], timeout=30) as response:
    pathlib.Path(sys.argv[2]).write_bytes(response.read(8*1024*1024))
PY
fi
export ARGUS_TEST_UPSTREAM="$temporary/upstream.tgz"
python3 "$ROOT/build_plugin.py" --upstream "$ARGUS_TEST_UPSTREAM" --output "$temporary/plugin.tgz"
tar -xzf "$temporary/plugin.tgz" -C "$temporary"
export ARGUS_TEST_PLUGIN_DIR="$temporary/package"
(cd "$ARGUS_TEST_PLUGIN_DIR" && npm install --ignore-scripts --omit=dev --package-lock=false --no-audit --no-fund)
(cd "$ROOT" && node --test --test-concurrency=1 test/*.test.mjs && python3 -m unittest discover -s test -p 'test_*.py' -v)
(cd "$HELPER_ROOT" && go test -mod=readonly -count=1 ./pkg/clientcredentials ./pkg/broker && go vet -mod=readonly ./pkg/clientcredentials ./cmd/spiffe-client-credentials)
for script in "$ROOT/../scripts/openclaw_spiffe_common.sh" "$ROOT/../scripts/connect_openclaw_openviking.sh" "$ROOT/../scripts/verify_openclaw_plugin_e2e.sh" "$ROOT/../scripts/openclaw_tdvm.sh" "$ROOT/../scripts/observe_openclaw_lifecycle.sh" "$ROOT/../../../core/spire/tests/tdvm/"*.sh; do bash -n "$script"; done
