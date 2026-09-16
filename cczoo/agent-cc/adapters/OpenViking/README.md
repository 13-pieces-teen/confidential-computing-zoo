# OpenViking adapter

The current Workload Attestation deployment uses TC API's
`nginx-spiffe-helper-v1` launch profile, the shared TDX Evidence Provider,
SPIRE WorkloadAttestor, Broker-aware SPIFFE Helper, and NGINX. See the
[current design](../../documents_ly/Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md),
[runbook](../../core/spire/workload/README.md), and
[validation records](../../core/spire/workload/VALIDATION.md).

TC API launches OpenViking, and the Workload tools register its actual service
process. Helper subscribes for that target's SVID through the local SPIRE Broker
API and delivers the credentials to NGINX. NGINX terminates mTLS on port 1943,
authorizes the exact OpenClaw SPIFFE ID, and forwards requests to OpenViking on
`127.0.0.1:1933` inside the service's network namespace.

The [OpenClaw client](../OpenClaw/spiffe_client/README.md) uses native HTTPS for
business requests. Historical designs and validation records remain in the
[documentation archive](../../documents_ly/archive/pre-workload-implementation/README.md).
