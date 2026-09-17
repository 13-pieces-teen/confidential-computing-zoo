# Argus Configuration Reference

Updated: 2026-09-16. Checked against `a0f19e0` and the current executable entry points.

This reference lists inputs consumed by the shipped binaries. Deployment commands
and generated SPIRE/Helper/NGINX configuration belong in the
[Workload runbook](../../spire/workload/README.md) and
[OpenClaw deployment manual](../../../adapters/OpenClaw/spiffe_client/DEPLOY-IP1-TDVM.md).

## Configuration entry points

| Executable | Configuration mechanism | Interface |
|---|---|---|
| `argus-evidence-provider` | Environment variables | HTTP `/health`, `POST /ra/v1/evidence` |
| `argus-guard` | Environment variables; a policy YAML file in `spiffe_identity` mode | HTTP health and the selected mode's decision API |
| `argus-spire-evidence-provider` | The explicit CLI options listed under [SPIRE Node Attestation](#spire-node-attestation) | Local UDS Node/Workload evidence routes |

The general Provider and Guard do not parse a service configuration YAML file or
CLI options such as `--config`, `--port`, `--help`, or `--version`. There is no
CLI/YAML/environment precedence chain. A library configuration type does not
become an executable setting unless the entry point loads it.

## Evidence Provider configuration

These are the defaults when launching `argus-evidence-provider` directly:

| Variable | Default | Effect |
|---|---|---|
| `HOST` | `0.0.0.0` | HTTP listen address |
| `PORT` | `8006` | HTTP port; an unparsable value falls back to 8006 |
| `TC_API_URL` | `http://localhost:8000` | TC API metadata endpoint; the literal value `disabled` disables this client |
| `TRUCON_TSM_REPORT_ROOT` | `/sys/kernel/config/tsm/report` | Linux TSM report-instance root used by this Provider's Quote generator |
| `TC_API_WORKLOAD_ID` | Unset | Preferred TC API metadata lookup key |
| `ARGUS_SERVICE_ID` | Unset | Local service ID; also the workload lookup fallback when `TC_API_WORKLOAD_ID` is empty |
| `CONTAINER_ID`, `HOSTNAME` | Unset | Container metadata lookup fallback, in this order; `HOSTNAME` also provides a fallback local service name |
| `ARGUS_SERVICE_NAME` | `HOSTNAME`, then `unknown` | Local service name |
| `ARGUS_IMAGE_DIGEST` | Unset | Local image claim; a missing `sha256:` prefix is added |
| `ARGUS_EXECUTABLE_DIGEST` | Unset | Optional local executable digest claim |
| `POD_UID`, `NAMESPACE`, `VM_INSTANCE_ID` | Unset | Optional local runtime claims |

TC API supplies metadata. A failed metadata lookup falls back to local service
identity; Quote generation still uses Linux TSM and propagates errors. It does
not obtain the Quote through a TC API attestation route.

The local binding observes the Provider process and environment. Setting an
identity or digest variable does not independently validate that claim, bind a
separate OpenViking process, or issue a SPIFFE identity. The current OpenViking
integration uses the separate SPIRE Provider and its protected instance
registration.

`start_argus.sh` overrides the Provider's default port to **8008** and points
Guard at `http://localhost:8008`. Those are wrapper defaults; the executable
defaults above remain **8006**. The wrapper also starts Guard on port 8007 and
uses `GUARD_HOST` for its listen address. See
[start_argus.sh](../start_argus.sh) when using that wrapper.

A direct launch on a TDX Guest can use:

```bash
HOST=127.0.0.1 PORT=8006 TC_API_URL=http://localhost:8000 \
  ./target/release/argus-evidence-provider
```

Source: [entry point](../src/bin/evidence_provider.rs),
[evidence engine](../src/service/engine.rs),
[local binding](../src/binding.rs), [TSM backend](../../tdx-quote/src/tsm.rs).

## Guard configuration

| Variable | Default | Effect |
|---|---|---|
| `GUARD_MODE` | `evidence` | Accepts only `evidence` or `spiffe_identity` |
| `HOST` | `127.0.0.1` | HTTP listen address |
| `PORT` | `8007` | Must be an integer from 1 to 65535 |
| `ARGUS_API_TOKEN` | Unset | Optional bearer token; required for a non-loopback listen address unless a token file is used |
| `ARGUS_API_TOKEN_FILE` | Unset | Read token from a file; mutually exclusive with `ARGUS_API_TOKEN` |
| `EVIDENCE_ENDPOINT` | `http://localhost:8006` | Evidence Provider base URL, used only in `evidence` mode |
| `INTEL_CA_CERT_PATH` | Required in `evidence` mode | Readable Intel CA certificate used by `RaAdapter` |
| `GUARD_SPIFFE_POLICY_FILE` | Required in `spiffe_identity` mode | Caller-local authorization policy YAML |

A configured token must be nonempty and contain no whitespace; a token file may
end with CR/LF. The token protects the decision APIs. The binary serves HTTP;
setting a token does not enable a TLS listener.

In `evidence` mode, Guard exposes `POST /ra/v1/verify` and
`POST /ra/v1/verify/batch`. The entry point constructs
`RaAdapter::with_intel_ca_cert` and `PolicyEvaluator::new()`; it does not load a
generic policy YAML or select a verifier from `VERIFIER_KIND`.
`BINDING_ASSURANCE_LEVEL`, `POLICY_STRICT_MODE`, `EVIDENCE_CACHE_TTL`,
`TRUSTEE_URL`, and `VERIFIER_TIMEOUT` are not runtime settings of this binary.
SPIRE's Trustee configuration is defined separately below.

```bash
HOST=127.0.0.1 PORT=8007 GUARD_MODE=evidence \
  EVIDENCE_ENDPOINT=http://localhost:8006 \
  INTEL_CA_CERT_PATH=/etc/argus/intel-ca.pem \
  ./target/release/argus-guard
```

Source: [Guard entry point and authentication](../src/bin/guard.rs),
[evidence policy implementation](../src/policy.rs).

## SPIFFE Guard policy YAML

In `spiffe_identity` mode, Guard exposes `POST /guard/v1/authorize` and
`GET /metrics`. It loads the policy once at startup. It evaluates the supplied
caller/target IDs, service, HTTPS origin and optional operation/data class; it
does not read peer certificates or verify a Quote. The caller must bind that
context to the actual authenticated transport.

The current OpenClaw native HTTPS transport and OpenViking NGINX/AuthZ path do
not call this standalone Guard. Adding this policy file alone does not add an
authorization step to that deployment.

Example policy (replace the HTTPS origin with the intended service origin):

```yaml
version: v1
policy_id: openclaw-openviking-v1
trust_domain: argus.local
decision_ttl_seconds: 30
rules:
  - id: openclaw-memory
    callers:
      - spiffe://argus.local/agent/openclaw
    target_spiffe_id: spiffe://argus.local/service/openviking-cmem
    target_service: openviking-cmem
    target_origins:
      - https://openviking.example:1943
    operations: [read, write]
    data_classes: [project-memory]
```

`version` must be `v1`; `decision_ttl_seconds` defaults to 30 and must be
between 1 and 300. At least one rule, caller and target origin are required;
rule IDs must be unique. SPIFFE IDs must belong to the policy trust domain,
and target origins must be HTTPS origins. Unknown YAML fields are rejected.

Rules match the caller, target ID, service and canonical origin, then any
operation/data-class constraint. Omitting `operations` or `data_classes`
(or using an empty list) leaves that field unrestricted. An unmatched request
is denied. Decision TTL is a returned authorization lifetime, not a Quote
cache or SVID lifetime setting. Restart Guard to load policy changes.

```bash
HOST=127.0.0.1 GUARD_MODE=spiffe_identity \
  GUARD_SPIFFE_POLICY_FILE=/etc/argus/spiffe-policy.yaml \
  ./target/release/argus-guard
```

Source: [policy schema, validation and matching](../src/spiffe_guard.rs).

## SPIRE Node Attestation

`argus-spire-evidence-provider` and the `argus_tdx` NodeAttestor plugins use
the inputs below. This is a configuration reference; deployment must also
provide the SPIRE Agent/Server, bootstrap trust, proof key, and Trustee policy.

| Component | Input | Contract |
|-----------|-------|----------|
| Provider CLI | `--agent-id` | Required SPIRE Agent ID; must equal the Server plugin's `agent_id`. It is fixed for the lifetime of the Provider process and cannot be supplied in `/ra/v1/node-evidence` requests. |
| Provider CLI | `--socket-path` | Defaults to `/run/argus/evidence-provider.sock`; the SPIRE Agent in the same TD must be able to access it. |
| Provider CLI | `--tsm-report-root` | Defaults to `/sys/kernel/config/tsm/report`; Linux TSM supplies the Quote. |
| Provider CLI | `--workload-registration-path` | Optional protected registration file enabling `POST /ra/v1/workload-evidence`; its Agent ID must match `--agent-id`. Requires `--workload-data-path` together. |
| Provider CLI | `--workload-data-path` | Approved absolute data mount destination in the OpenViking container; required with workload registration. Configuration and executable paths must remain outside writable mounts. |
| Agent plugin HCL | `evidence_socket_path` | Absolute path matching the Provider socket. |
| Agent plugin HCL | `proof_key_path` | Absolute path to a regular PKCS#8 Ed25519 `PRIVATE KEY` PEM file with `0600` permissions on Linux. |
| Server plugin HCL | `agent_id` | Same identity as the Provider; its trust domain must match SPIRE's core `trust_domain`. |
| Server plugin HCL | `slot_owner_key_sha256` | SHA-256 of the Agent's raw 32-byte proof public key, encoded as 64 lowercase hexadecimal characters. This key is separate from SPIRE's SVID key. |
| Server plugin HCL | `trustee_url`, `trustee_ca_path`, `trustee_server_name` | HTTPS origin, trusted CA file, and TLS server name for the Trustee endpoint. |
| Server plugin HCL | `ear_public_key_path`, `ear_expected_issuer`, `ear_expected_profile`, `policy_id` | Independent P-256 EAR verification key and expected appraisal claims from the configured Trustee deployment. |

An Agent ID has the form
`spiffe://<trust-domain>/spire/agent/argus_tdx/<node-id>`, for example
`spiffe://example.org/spire/agent/argus_tdx/worker-01`. The trust domain accepts
lowercase letters, digits, `.`, `_`, and `-`; the nonempty node ID accepts
letters, digits, `.`, `_`, and `-` and cannot consist only of dots. Ports,
escapes, query strings, fragments, and extra path segments are rejected. The
complete UTF-8 ID is limited to 65,535 bytes by the protocol's `LP16` encoding.
Rust and Go tests consume the same identity acceptance cases and binding vector.

This configuration supports one pinned Agent slot. A node may host multiple
services, but admitting additional SPIRE Agents requires an enrollment design
with distinct identities and proof-key pins. Service SVIDs require workload
attestation and registration policy. A SPIFFE trust domain is an identity
namespace; it is distinct from the TDX trust domain (TD) containing the Agent.

The combined OpenViking deployment renders Provider, WorkloadAttestor, Helper,
entries and policy from one explicit deployment configuration. Different valid
Agent IDs are accepted only when they match the configured deployment; an
unapproved identity is rejected before Quote generation or selector issuance.
The workload schema and its SHA-384 REPORTDATA encoding remain v1.
Both SPIRE-mode evidence routes remain under `/ra/v1/`, without unversioned
aliases. See the [Workload runbook](../../spire/workload/README.md) for the
combined build, Provider unit, and deployment checks.

Only the listed SPIRE Provider options are parsed; this entry point has no
`--help` or `--version` handler. Unknown arguments fail startup. Its TSM root
is supplied by `--tsm-report-root`, not the general Provider's environment
variable.

Source: [SPIRE Provider CLI](../src/bin/spire_evidence_provider.rs),
[Node plugins](../../spire/plugins/argus-tdx-nodeattestor/).

## Logging and deployment checks

The Rust binaries initialize `tracing_subscriber::fmt`. Use `RUST_LOG` to
filter logs, for example `RUST_LOG=argus=debug`. No entry point reads
`RUST_LOG_FORMAT` or switches to a JSON formatter from configuration.

For the general HTTP services, check the actual process environment and
`GET /health`. A healthy listener alone does not establish successful Quote
generation, verification, or business authorization.

For SPIRE deployment, use the [Workload runbook](../../spire/workload/README.md):
it covers approved measurements, trust material, HCL generation, Entry checks,
protected workload registration and lifecycle validation. Protocol details
belong in the [Node](../../../documents_ly/Argus-TDX-Node-Attestation-CN.md) and
[Workload](../../../documents_ly/Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md)
documents; execution outcomes belong in dated validation reports.
