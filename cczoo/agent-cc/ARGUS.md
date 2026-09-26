# Argus code map

Argus implements instance admission and the lifecycle of protected service communication within Agent-CC. Start here for code navigation; deployment commands remain in their component runbooks.

```text
agent-cc/
  core/
    argus/                Rust evidence provider, collection and policy bindings
    spire/
      plugins/            Node and Workload attestors
      helpers/            Broker, credentials, watchdog and exact-ID AuthZ
      workload/           Service deployment, registration, policy, systemd and tests
    tc_api/               Controlled launch, profile projection and runtime events
    tlog/                 Signed event/history verification primitives
    trust-service/        Trustee/attestation service integration
    tdx-quote/            Hardware Quote integration
  adapters/
    OpenClaw/spiffe_client/  Gateway deployment, native transport and business acceptance
    OpenViking/receiver_audit/  Application-read observation and collector
  experiments/argus/      E1–E5 tools, isolated experimental variants and analysis
    config/              Runtime configuration fragments
    examples/            Editable example inputs and fixed scenario recipes
    tests/               Local regression fixtures and component integration tests
    static-client/       Experiment-only static credential build sources
  documents_ly/          Research notes and manuscript working material
```

## Where to change behavior

| Change | Primary location | Validation / operator entry |
|---|---|---|
| Node enrollment or valid identity renewal | [SPIRE integration](core/spire/README.md), `core/spire/plugins/argus-tdx-nodeattestor` | Node operator script and plugin tests |
| Quote/current-instance evidence | [Evidence provider](core/argus/src/bin/spire_evidence_provider.rs) | Rust evidence tests and real TDX acceptance |
| Workload admission and history policy | [Workload architecture](core/spire/workload/ARCHITECTURE.md), `core/spire/workload/trustee` | Workload policy/provider tests and production verify |
| Service deployment, launch recovery and ingress | [Workload runbook](core/spire/workload/README.md) | `workload.py`, systemd templates and `workload/tests` |
| Credential lifecycle and precise client identities | [Helper](core/spire/helpers/spiffe-helper/README.md) | Go AuthZ/Broker/client credentials/sidecar tests |
| Controlled launch/profile | [TC API](core/tc_api/README.md) | Follow `core/tc_api/AGENTS.md`; service/profile tests |
| Gateway transport or per-client business scope | [Client runbook](adapters/OpenClaw/spiffe_client/README.md), [fleet acceptance](adapters/OpenClaw/spiffe_client/FLEET-ACCEPTANCE.md) | Client Node/Python tests and six-stage business acceptance |
| Actual application-read observation | [Receiver audit](adapters/OpenViking/receiver_audit/README.md) | IPC/ASGI tests; remote independent receive evidence |
| Experiments and statistics | [Experiment structure](experiments/argus/STRUCTURE.md) | [Validation](experiments/argus/VALIDATION.md), [two-host runbook](experiments/argus/REMOTE-RUNBOOK.md) |

## Dependency and execution boundaries

Production deployment uses the evidence provider, SPIRE/Helper, controlled launch and application adapters. Experimental variants and fault injection live under `experiments/argus`; ordinary deployment does not import the experiment runner. Experimental installation may copy a verified production payload and apply an explicitly named variant.

`core/argus` is the existing Rust component, not a second repository root. Keeping this name and the current CLI paths avoids changing installed unit paths and build contracts. Shared Python modules in the experimental directory are local helpers for its existing commands, not another long-running service.

The receiver collector observes application reads; collector failure does not gate application delivery. Business user/resource permission remains in OpenViking. Keep `CanReattest=false` and the approved policy; this code map adds no admission requirement.

Runtime credentials, datasets, generated evidence, temporary test directories and research drafts are not production source inputs. Keep them outside the committed example/configuration directories. `documents_ly` is an independent working area, not a deployment prerequisite.

Local PASS results validate the tested software behavior. Real TDX, deployed ingress stop times, model memory results and cross-host performance require the remote evidence described in the runbook.
