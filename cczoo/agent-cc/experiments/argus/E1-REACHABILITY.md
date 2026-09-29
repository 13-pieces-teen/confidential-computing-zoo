# E1 historical-policy reachability record

Aligned with Feishu revision 1329. Run this alongside the single-client
continuous-task pilot. It is a short record of actual operations and evidence,
not a new orchestrator or an instruction to weaken production checks.

The question is whether two real trajectories pass the same current-fact and
local checks, while the approved lifecycle policy distinguishes their histories.
A signed fixture demonstrates a verifier rule. Additional online protection
requires a reachable receiver and a difference after the common checks.

## 1. Fix the case and authority before executing it

Record the delivered commit/build hashes, approved policy file/hash, workload,
logical SPIFFE ID and initial target. Retain the current policy, including its
approved platform exceptions; do not add a `TCB UpToDate` requirement.

Choose one case below and record the principal executing **each** operation:

| Candidate | What it can establish |
|---|---|
| Approved target plus allowed unrelated measured activity | History tolerance and fixed-measurement diagnostic; not an attack. |
| Approved new launch with the saved old registration | The actual rejection stage; common local rejection is common protection. |
| Approved new launch with fresh registration | Independent legal admission, not a historical-policy violation. |
| Controlled stop/start of the same container and fresh registration | Candidate current-fact/history distinction, subject to all common checks. |
| Different listener within the same network namespace | A separate route/binding case; do not assume an ordinary interface permits it. |
| Edited history, hidden stop or a mismatched captured Quote/EAR | Offline diagnosis unless a real allowed submission path is demonstrated. |

For each step identify the interface, permissions and actual command/request
artifact. An ordinary deployment participant may use only its declared interface.
Operator root, Docker administration, Helper freezing and collector interruption
are authorized experiment capabilities, not powers automatically attributed to
that participant. Do not bypass the measured Docktap path or rewrite registration
files to make a case reachable.

## 2. Collect the existing evidence at each boundary

1. Use [E1-REAL-RUNS.md](E1-REAL-RUNS.md) for baseline admission and before/after
   observations. Save launch/container, PID/start time, namespace, approved
   configuration, registration, Helper invocation/subscription and SVID serial.
2. Run the selected operation through its actual permitted path. Save the event
   and launch records. Distinguish the command's start/completion from the
   observed process change. Record every failure rather than replacing it with
   an expected verdict.
3. Observe registration, current-target check, Provider collection, Trustee
   appraisal, selector/identity delivery and ingress readiness separately. Mark
   the first rejection, unavailability or stage not reached. A new subscription
   for the same instance also generates fresh evidence; it does not imply a new
   container or launch.
4. Preserve optional originals by nonce using
   [ADMISSION-ARCHIVE.md](ADMISSION-ARCHIVE.md). Record missing material explicitly:
   a rejection before EAR creation cannot provide a signed accepted EAR. Archive
   replay does not rerun DCAP or establish a new live admission. Keep fixture,
   captured-context and live observations separate.
5. Record the **actual** ingress namespace and upstream route/listening process
   before and after the operation. NGINX joins the registered target network
   namespace; a newly created container does not automatically receive traffic
   sent through the old ingress. If the route cannot reach the candidate, retain
   that result. Do not redirect the proxy merely to manufacture an attack path.
6. Release a new synthetic fact after the instance change. Use the existing
   passive receiver and request/fact IDs to associate reads with the actual
   process/deployment; neither a TLS response nor a client-provided instance
   label proves who read it. Record full facts, partial bytes and coverage gaps.
   Do not claim zero receipt without complete coverage. A confirmed unexpected
   read remains a violation even if another interval is unknown.

## 3. Result template (one record per trajectory)

Copy this table into the private evidence directory and replace `NOT_RUN` with
observations and file/hash references. It is a human review record, not a machine
accepted admission result.

| Field | Actual observation / evidence |
|---|---|
| Run, case, group, commit/build | NOT_RUN |
| Approved policy path/hash and relevant history rule | NOT_RUN |
| Actor, permissions, permitted interface per operation | NOT_RUN |
| Experiment class: ordinary-interface attempt / operator fault / offline diagnosis | NOT_RUN |
| Operation command/request, start and completion | NOT_RUN |
| Before/after launch, container, PID/start, namespace, image/config | NOT_RUN |
| Shared checks: registration, target, Provider, current software/config | NOT_RUN |
| First rejection/unavailable stage and actual reason | NOT_RUN |
| Quote, challenge, EAR, policy/history bundle, replay result | NOT_RUN |
| Subscription/Helper invocation, SVID, readiness | NOT_RUN |
| Ingress namespace, upstream route and actual listener | NOT_RUN |
| Old route reaches candidate: observed yes / no / unknown | NOT_RUN |
| Fact release, unique full facts / partial bytes by receiving process | NOT_RUN |
| Receiver coverage and clock uncertainty | NOT_RUN |
| Ordinary-interface online reachability supported: yes / no / unknown | NOT_RUN |
| Historical difference supported: diagnostic only / reachable policy difference / none / unknown | NOT_RUN |

Compare the two trajectories with the same image/software policy, capture shape,
input workload and local controls. List unavoidable launch/container/PID changes.
Current-facts-only is an entire-history ablation: it would remove signature,
predecessor, inclusion, RTMR replay and launch/successor eligibility checks while
retaining fresh Quote/current-fact binding, software/configuration policy and
local control. Do not attribute that total difference to one subcondition.

## 4. Stop and extension rules

- If a shared collector, current-target check or namespace route rejects the
  trajectory, report common protection or unreachability. Do not disable it.
- If only edited/signed fixtures produce the distinction, retain an offline
  policy-coverage result. Do not extend it into an online attack or Agent benefit.
- Only after a real path reaches the remote policy decision with all common
  checks satisfied should an isolated current-facts-only research build be
  designed. It remains `proposed_not_run` here; no production skip-history switch
  is supplied and it is not an E4 prerequisite.
- Helper freeze tests required-supervision loss, not proof that launch history
  became invalid. Keep that stopping condition separate from an unadmitted
  replacement's receipt. A successful legal re-admission is a third outcome.
- Transport lanes (new, reused, in-flight) are not Attest-on-connect or Invocation
  lease implementations. Those mode comparisons remain `proposed_not_run` and
  would require a separate controlled change of appraisal timing and supervision.

Remote execution and all actual receiver/TDX outcomes remain `NOT_RUN` until
their evidence is collected. Equal outcomes against the strong baseline are
valid results.
