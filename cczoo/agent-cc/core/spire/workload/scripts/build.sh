#!/usr/bin/env bash
set -euo pipefail
WORKLOAD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPIRE_ROOT="$(dirname "$WORKLOAD_ROOT")"
OUT="${ARGUS_WORKLOAD_BUILD_DIR:-$WORKLOAD_ROOT/build}"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo "TDX build requires Linux x86_64" >&2; exit 1; }
mkdir -p "$OUT/bin"
OUT="$(cd "$OUT" && pwd)"
for module in "$WORKLOAD_ROOT" "$SPIRE_ROOT/plugins/argus-tdx-nodeattestor" "$SPIRE_ROOT/plugins/argus-tdx-workloadattestor" "$SPIRE_ROOT/helpers/spiffe-helper"; do
    (cd "$module" && go test ./...)
done
(cd "$SPIRE_ROOT/helpers/spiffe-helper" && ARGUS_NGINX_TESTS=1 go test ./pkg/authz -run TestNGINXMTLSAuthzAndRotation -count=1)
python3 -m unittest discover -s "$WORKLOAD_ROOT/tests" -v
PYTHONPATH="$SPIRE_ROOT/../tc_api:$SPIRE_ROOT/../tlog${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m pytest "$SPIRE_ROOT/../tc_api/tests/test_workload_profile.py" \
    "$SPIRE_ROOT/../tc_api/tests/test_workload_launch_flow.py" -q
(cd "$SPIRE_ROOT/plugins/argus-tdx-nodeattestor" && go build -trimpath -o "$OUT/bin/argus-tdx-nodeattestor-agent" ./cmd/agent && go build -trimpath -o "$OUT/bin/argus-tdx-nodeattestor-server" ./cmd/server)
(cd "$SPIRE_ROOT/plugins/argus-tdx-workloadattestor" && go build -trimpath -o "$OUT/bin/argus-tdx-workloadattestor" ./cmd/argus-tdx-workloadattestor)
(cd "$WORKLOAD_ROOT" && go build -trimpath -o "$OUT/bin/argus-workload" ./cmd/argus-workload)
(cd "$SPIRE_ROOT/helpers/spiffe-helper" && go build -trimpath -ldflags '-X github.com/spiffe/spiffe-helper/pkg/version.gittag=0.11.0-argus.1' -o "$OUT/bin/spiffe-helper" ./cmd/spiffe-helper)
for tool in argus-agent-config spiffe-authz spiffe-mtls-probe; do
    (cd "$SPIRE_ROOT/helpers/spiffe-helper" && go build -trimpath -o "$OUT/bin/$tool" "./cmd/$tool")
done
(export CARGO_TARGET_DIR="$OUT/cargo-provider"; cd "$SPIRE_ROOT/../argus" && cargo test --locked --bin argus-tdx-evidence-provider && cargo build --locked --release --bin argus-tdx-evidence-provider)
install -m 0755 "$OUT/cargo-provider/release/argus-tdx-evidence-provider" "$OUT/bin/"
(export CARGO_TARGET_DIR="$OUT/cargo-trustee"; cd "$WORKLOAD_ROOT/trustee-contract" && cargo test --locked)
archive="$OUT/spire-1.15.3-linux-amd64-musl.tar.gz"
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
    https://github.com/spiffe/spire/releases/download/v1.15.3/spire-1.15.3-linux-amd64-musl.tar.gz -o "$archive"
printf 'ca1a4d1155317bdd2afc7f36663828a10410c7c840e54725b90b4064b0a301c7  %s\n' "$archive" | sha256sum --check
tar -xzf "$archive" -C "$OUT"
[[ "$("$OUT/spire-1.15.3/bin/spire-agent" -version 2>&1)" == 1.15.3 ]]
[[ "$("$OUT/spire-1.15.3/bin/spire-server" -version 2>&1)" == 1.15.3 ]]
SPIRE_BIN_DIR="$OUT/spire-1.15.3/bin" ARGUS_WORKLOAD_TOOLS_DIR="$OUT/bin" \
    python3 -m unittest discover -s "$WORKLOAD_ROOT/tests" -p test_spire_cli.py -v
(cd "$OUT" && sha256sum bin/* > SHA256SUMS)
printf 'BUILD=PASS\nOUTPUT=%s\n' "$OUT"
