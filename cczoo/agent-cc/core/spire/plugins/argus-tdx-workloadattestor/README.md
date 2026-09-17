# Argus TDX WorkloadAttestor

SPIRE v1.15.3 Agent plugin for the host-managed OpenViking workload flow. It
implements `AttestReference` for a Broker `WorkloadPIDReference`; ordinary
Workload API `Attest` calls receive no trusted selectors.

The plugin loads the protected target registration, checks the approved Agent,
workload, policy and content digests, requests fresh evidence over the local
Provider UDS, and verifies the signed Trustee EAR and binding. The process
instance is checked again before selectors are returned. Node admission is a
separate prerequisite; SVID rotation is not a fresh attestation.

## Configuration

Use the [deployment runbook](../../workload/README.md) and its single
`environment.json` input to generate the Agent HCL. The `argus_tdx` plugin data
requires these fields; unknown fields fail configuration.

| Field | Contract |
|---|---|
| `agent_id` | Expected `spiffe://<trust-domain>/spire/agent/argus_tdx/<node-id>`; must match registration, Provider and Node deployment. |
| `workload_id`, `policy_id` | Approved workload and Trustee policy identifiers. |
| `image_config_digest`, `config_digest` | Approved lowercase `sha256:` digests, not image tags or registry-name hashes. |
| `target_registration_path` | Protected absolute registration file path. |
| `evidence_endpoint` | `unix:///absolute/socket`; requests use `/ra/v1/workload-evidence`. |
| `trustee_endpoint` | Direct HTTPS origin. |
| `trustee_ca_path`, `trustee_server_name` | Explicit TLS trust anchor and peer name. |
| `ear_public_key_path`, `ear_expected_issuer`, `ear_expected_profile` | Explicit EAR verification key and expected claims. |
| `request_timeout` | Default `20s`; greater than zero and at most `60s`. |
| `max_response_bytes` | Default 2 MiB; greater than zero and at most 4 MiB. |

No example Agent ID is a built-in authorization. A different syntactically valid
ID still fails unless it equals the approved configuration. Identity, paths and
ports can change without modifying the `argus.workload.tdx.v1` evidence schema
or Node challenge/PoP contract. The application checks remain OpenViking-specific.

## Verification

Run `go test ./...` and `go vet ./...` from this module. The shared Go/Rust vectors
under `../../workload/testdata/` cover both the example and an alternative
deployment. Tests substitute evidence/Trustee transports; hardware acceptance
requires the company TDVM procedure in the runbook.
