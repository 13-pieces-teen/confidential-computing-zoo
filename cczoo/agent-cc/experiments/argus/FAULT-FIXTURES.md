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
