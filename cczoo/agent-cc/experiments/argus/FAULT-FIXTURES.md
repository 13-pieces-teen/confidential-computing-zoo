# Configuration and process/listener replacement fixtures

`fault_fixture.py` runs only on the isolated experiment service host as Linux
root. Its deployment's `workload.config_host_path` must reside beneath
`/srv/argus-experiments`, with root-owned, non-writable parent directories.
Production paths are rejected. Existing Helper-freeze/crash/target-exit fixtures
continue to use `remote_acceptance.py`.

The protected deployment's `server_script` must name its installed
`paths.install_dir/scripts/workload.py`. The fixture and lifecycle snapshot
tools verify that package's build manifest and protected executable entrypoints.
Full source inventory and hash checks run during installation and preflight.
Configuration, manifest, executable files and their ancestor directories must be
root-owned without group/world write or symlink traversal. Each CLI process
loads one installed arm through normal
Python imports, after checking its generated unit mapping. Run a fresh command
for another arm; preloaded deployment modules are rejected, including cached
production imports. The tool does not replace Python's import mechanism.

Start the independent receiver and both real client TLS lanes first. Use the
same run ID; require three successful requests per lane and the declared
application milestone before injecting either fault. Fault files are compatible
with `remote_acceptance.py check` and `release`. The coordinated `fault_trial.py`
can dispatch these fixtures when configured with `server_fault_fixture` and the
declared source files; otherwise run these explicit commands after PROBE_READY.

## Bound configuration mutation

Prepare separate protected **copies** of the approved original and a syntactically
valid replacement configuration. Both may contain credentials: use mode `0600`
under a protected operator directory and never publish their contents. The tool
records paths and SHA256 only. Do not use hard links to the active file. The
replacement must differ from the approved original; a synthetic extra field is
sufficient for the local configuration-byte monitor even if OpenViking ignores it.

```sh
python3 fault_fixture.py inject \
  --config /etc/argus-experiment/environment.json \
  --event config-change --run-id trial-a \
  --original /srv/argus-experiments/trial-a/fixtures/original.json \
  --original-sha256 ORIGINAL_SHA256 \
  --replacement /srv/argus-experiments/trial-a/fixtures/replacement.json \
  --replacement-sha256 REPLACEMENT_SHA256 \
  --output /var/lib/argus-evidence/trial-a/fault.jsonl \
  --execute-fault --hold-recovery
```

Before mutation the tool invokes the real current-target checker twice and
installs a uniquely owned experiment `Restart=no` hold. It persists an exclusive
mutation intent before changing bytes. It opens the active inode without following
symlinks, locks it, matches the target's `/proc/PID/root` file inode and original
hash, writes in place and fsyncs. An atomic rename is deliberately unsuitable:
Docker's file bind mount would keep the old inode and produce a false experiment.

After both probe lanes and receiver observation finish, restore explicitly:

```sh
python3 fault_fixture.py restore \
  --config /etc/argus-experiment/environment.json \
  --fault /var/lib/argus-evidence/trial-a/fault.jsonl --execute-restore
```

Restore requires the same process incarnation, namespace, boot, cgroup and
executable, unchanged deployment and source-file hashes, and a current active
file matching either the original or declared replacement. It never overwrites
an unexpected third-party change. A truncated checkpoint, partial unknown file
write, repeated restore intent or replaced/exited target requires explicit
operator reconciliation; rerunning the injector is not recovery. Restoring the
file does not restart services or re-admit a workload. Release this run's hold
using `remote_acceptance.py release`, then perform normal registration/admission
and business verification.

## Same-container process/network/listener replacement

```sh
python3 fault_fixture.py inject \
  --config /etc/argus-experiment/environment.json \
  --event same-container-restart --run-id trial-b \
  --output /var/lib/argus-evidence/trial-b/fault.jsonl \
  --execute-fault --hold-recovery
```

This executes one fixed `docker restart --time 0 <checked full container ID>`,
preserving the configured Docker/Docktap environment. It checks launch/workload/
image association and records the changed Docker `StartedAt` of the same
container. No arbitrary shell/Docker-exec operation is accepted. Record this as
**same-container process and network/listener incarnation replacement**, not
an isolated port move. The old target registration and collector process binding
cannot authorize the restarted process. Do not immediately rebind either during
the stop-observation window. A new receiver process attempting to use the old
collector produces an incomplete window, never fabricated zero-delivery evidence.

After collection release the hold and reconcile through the existing
re-admission workflow. A restart carrying the old launch ID may be rejected by
the production history verifier when a successful stop was recorded. A valid
new launch then needs the ordinary launch path; this fixture does not promise
automatic recovery or manufacture new launch evidence.

Local tests mutate only temporary JSON fixture inodes and stub systemd/Docker
actions. Real monitor detection, shutdown and post-bound delivery remain NOT_RUN
until executed on the remote experiment instance.

## Original backend liveness during E2

Set `timeline.backend_probe: true` in `fault-trial` (enabled in the example).
The server observer first obtains the checked target registration, then performs
fixed, unauthenticated `GET /health` requests to `127.0.0.1` in that original
process's network namespace. It requires Linux root, checks PID/start time,
boot, network namespace and owned listening-socket inodes before and after each
request, and never reuses the approved configuration digest as a liveness test.
The request carries no private fact, API key, query, or request body. It creates
no route, listener, firewall exception or reusable forwarding entry. Response
content is not archived. It does not probe a replacement process automatically.

The fixed health handler must be available in the pinned service build; absence,
timeout, identity change, non-200 response and explicit unhealthy response remain
`UNKNOWN`, never proof of ingress protection. When enabled, this probe must
succeed in the initial healthy baseline before fault injection. Its polling cost
and gaps are included in the finite observation cadence; keep it identical across
the compared arms.

`timeline.backend_availability` reports positive samples after the fault and
after observed entry closure separately. `live_after_entry_stop=OBSERVED`
establishes that the original application still answered its health endpoint at
those sampled times. It does not establish uninterrupted memory availability or
memory-result correctness. Combine it with actual post-fault input releases,
receiver reads, the original TLS lane and the targeted no-close control. If the
backend has died, zero observed private reads must not be described as an effect
of active ingress closure. Independent policy eligibility remains a separate
`UNKNOWN` when the required eligibility interval is absent.
