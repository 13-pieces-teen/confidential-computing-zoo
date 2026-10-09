#!/usr/bin/env bash
# E1 deterministic business probe (IP1 client guest). No LLM. Keys stay on guest.
# Usage: e1-business-probe.sh [label]
set -u
LABEL="${1:-manual}"
TS="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
INSTANCE=paper01full
CRED="/run/argus-openclaw/instances/${INSTANCE}/credentials"
OUT="/root/argus-openclaw-evidence/e1-probes/probe.jsonl"
CONTAINER=argus-oc-paper01full
CONFIG_PATH=/home/node/.openclaw/openclaw.json
FACT_SHA=a12f3a938f164bc83682af72edd9a9be52b2ab0f5aa7520b749a3ecfcb8fe1f9

if [ ! -f "${CRED}/ready.json" ]; then
  echo "{\"label\":\"${LABEL}\",\"observed_at\":\"${TS}\",\"error\":\"NO_READY_JSON\"}" >> "${OUT}"
  exit 1
fi
GEN="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["generation"])' "${CRED}/ready.json" 2>/dev/null)"
GENDIR="${CRED}/${GEN}"
cli_serial="$(openssl x509 -in "${GENDIR}/svid.pem" -noout -serial 2>/dev/null | cut -d= -f2)"
cli_uri="$(openssl x509 -in "${GENDIR}/svid.pem" -noout -ext subjectAltName 2>/dev/null | grep -o 'URI:spiffe://[^,]*' | head -1 | cut -c5-)"

# --- A. direct mTLS /health to origin, live client SVID, peer cert captured ---
A="$(python3 - "${GENDIR}" <<'PYEOF'
import json, ssl, socket, subprocess, sys
from pathlib import Path
g = Path(sys.argv[1])
rec = {"tls": "error"}
try:
    ctx = ssl.create_default_context(cafile=str(g / "bundle.pem"))
    ctx.check_hostname = False
    ctx.load_cert_chain(certfile=str(g / "svid.pem"), keyfile=str(g / "key.pem"))
    with socket.create_connection(("10.0.2.2", 1944), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname="10.0.2.2") as tls:
            der = tls.getpeercert(binary_form=True)
            p = Path("/tmp/e1-peer.der"); p.write_bytes(der)
            r = subprocess.run(["openssl", "x509", "-inform", "DER", "-in", str(p), "-noout",
                                "-serial", "-ext", "subjectAltName"], capture_output=True, text=True)
            rec["peer_cert"] = (r.stdout or r.stderr).strip()[:600]
            tls.sendall(b"GET /health HTTP/1.1\r\nHost: 10.0.2.2\r\nConnection: close\r\n\r\n")
            data = b""
            while True:
                chunk = tls.recv(65536)
                if not chunk:
                    break
                data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    rec.update({"tls": "ok",
                "http_status": int(head.split()[1]) if head.startswith(b"HTTP/1") and len(head.split()) > 1 else None,
                "body_bytes": len(body), "body_head": body[:120].decode("utf-8", "replace")})
except Exception as e:
    rec.update({"tls": "error", "error": (type(e).__name__ + ": " + str(e))[:200]})
print(json.dumps(rec))
PYEOF
)"

# --- B. gateway transport business probes (no LLM; retrieval only) ---
B1="$(sudo docker exec -u 21001 -e OPENCLAW_CONFIG_PATH="${CONFIG_PATH}" -e OPENVIKING_API_KEY="$(tr -d '\n' < /tmp/p0-c-business.key)" "${CONTAINER}" node /home/node/e1-probe.mjs '{"route":"/api/v1/sessions","identity_probe":true}')"
B2="$(sudo docker exec -u 21001 -e OPENCLAW_CONFIG_PATH="${CONFIG_PATH}" -e OPENVIKING_API_KEY="$(tr -d '\n' < /tmp/p0-c-business.key)" "${CONTAINER}" node /home/node/e1-probe.mjs '{"query":"ARGUS-DUAL-TDVM-E2E-p0-20261002t1521z-sf-a1","fact_sha256":"'"${FACT_SHA}"'"}')"

LABEL="$LABEL" TS="$TS" GEN="$GEN" CLI_SERIAL="$cli_serial" CLI_URI="$cli_uri" A="$A" B1="$B1" B2="$B2" python3 - "${OUT}" <<'PYEOF'
import json, os, sys
out = sys.argv[1]
def load_raw(s):
    try:
        return json.loads(s)
    except Exception:
        return {"raw": s[:500]}
rec = {
    "schema": "argus.e1-business-probe.v1",
    "label": os.environ["LABEL"],
    "observed_at": os.environ["TS"],
    "generation": os.environ["GEN"],
    "client_serial": os.environ["CLI_SERIAL"],
    "client_uri": os.environ["CLI_URI"],
    "mtls_health": load_raw(os.environ["A"]),
    "gateway_sessions": load_raw(os.environ["B1"]),
    "gateway_search": load_raw(os.environ["B2"]),
}
with open(out, "a") as f:
    f.write(json.dumps(rec) + "\n")
print("probe appended:", os.environ["LABEL"], os.environ["TS"], "gen=" + os.environ["GEN"], "serial=" + os.environ["CLI_SERIAL"])
PYEOF
