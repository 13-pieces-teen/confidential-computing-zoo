# Isolated experiment deployments

`variants.py` creates runnable copies of the deployment package. It does not add
a weak-mode option to the production installer or Helper. Run arms sequentially
on the same approved TDX node, with the same application configuration, data and
application API permissions. The Node identity, durable SPIRE data and proof key
remain unchanged. Do not delete Node state between arms.

| CLI name | Actual change in the generated package |
| --- | --- |
| `full_argus` | Normal Workload plugin, Broker, local target checks, watchdog and active NGINX closure. |
| `native_spire_guarded` | Separately compiled HCL merger removes custom Workload plugin and installs official unix/Docker. Target Entry requires exact UID, executable path, Docker image config digest and `io.trucon.workload-id` label. Same Broker, target monitor, watchdog, cleanup and NGINX are retained. Workload Trustee route/appraisal-log checks are not used; TDX Node remains. |
| `no_watchdog` | Remove systemd watchdog and its required-environment setting only; in-process health checks, target checks, exit cleanup, unit dependencies and ingress stop remain. |
| `no_close` | Remove Helper stop hook action, stop ExecStopPost, and all NGINX BindsTo/Requires/PartOf relations. Keep watchdog, target checks and credential cleanup. Explicit experiment teardown still stops everything **after** the observation window. |
| `static_mtls` | Dedicated static credentials, same NGINX/AuthZ and application endpoint. No Agent/Provider/Helper runs for this arm. Startup still validates the chosen target; this is a cost reference, not an instance-attestation mechanism. |

Official Docker selectors are documented for the pinned SPIRE version at
[SPIRE v1.15.3 Docker WorkloadAttestor](https://raw.githubusercontent.com/spiffe/spire/v1.15.3/doc/plugin_agent_workloadattestor_docker.md).
In particular, `image_config_digest` is used; no invented `container_id` selector
or mutable `image_id` tag is treated as an immutable instance identity. Native
UID defaults to 0 and must be changed with `--native-uid` when the approved target
uses another UID. The baseline does not remotely prove the workload history.

## Build and render

First build the complete production Linux payload using the current source and
`core/spire/workload/scripts/build.sh`. The existing normal build verifier checks
all 12 binaries and installed source before an experimental payload is derived.
Native rendering additionally needs Go and compiles a separate Linux amd64 HCL
merger. Its source and resulting binary digest are in the experiment manifest.

```sh
python3 experiments/argus/variants.py render \
  --variant full_argus --experiment-id paper01 \
  --config /etc/argus-workload/environment.json \
  --build-dir core/spire/workload/build --output /root/argus-arms/paper01/full
python3 experiments/argus/variants.py inspect --output /root/argus-arms/paper01/full
```

The config must reference the **existing explicitly approved policy artifact and
its digest**. Rendering never synthesizes a different policy or adds UpToDate.
Without `--build-dir`, rendering creates an inspectable source-only package;
`payload_ready=false` and installation is forbidden. This enables local source
tests without pretending a Linux/TDX payload exists. Each output directory is
immutable and must not exist before render. Save later run evidence elsewhere.

Every arm gets distinct Helper/target/client SPIFFE IDs under
`/experiment/<experiment-id>/<arm-short-name>`, service names, installation,
configuration and evidence directories. `environment.json` is the source of
truth for the generated identities. Configure the actual clients with these
IDs and their own experiment credentials before the arm; existing production
client certificates intentionally do not authorize against this identity set.
No weaker Entry is ever created with the production target ID.

Paths/units are isolated; ports, application data and Node data are deliberately
shared for paired sequential measurements. Stop the preceding arm and verify it
is inactive before the next arm. Do not run multiple Agent processes on the same
Node data directory. Do not insert experiment registration entries under another
arm's SPIFFE ID.

## Install and run

```sh
sudo python3 experiments/argus/variants.py install \
  --output /root/argus-arms/paper01/full --execute-experiment
```

This installs but does not launch a container or start the services. Install the
same derived payload on the control-plane side as necessary for exact Helper
hash checking, and use the generated `server_script`/`server_config` paths for
`apply-entries` and `server-check`. These paths must exist on the Server host.
Never call production lifecycle scripts with a weaker arm's config: execute the
generated installed `scripts/workload.py` throughout that arm.

Use `resume-launch --launch-id <verified-existing-id>` to associate an existing
approved test target with the arm, then `register`, `preflight`, `start`, `verify`
and `manifest`. Deliberate new targets use the existing single-submit launch
protocol. Do not delete `launch-state.json` to rerun an uncertain POST. Changing
to an audit-derived image still requires a separately approved image/config
digest before deployment; this generator does not approve new measurements.

After startup, run `inspect --live` as root on the service machine. It compares
installed binaries/scripts/configuration, loaded unit fragment files, actual
systemd dependency/watchdog properties and rebuilt Agent HCL. Unreviewed systemd
drop-ins fail inspection. Take this snapshot **before** a fault tool installs its
known temporary no-restart hold. Collect the fault tool's hold receipt separately,
then release it and repeat live inspection after recovery.

The static arm additionally requires a JSON file supplied via
`--static-credentials`, containing protected absolute file references:

```json
{"cert":"/etc/argus-test-ca/server.pem","key":"/etc/argus-test-ca/server-key.pem","bundle":"/etc/argus-test-ca/bundle.pem"}
```

Use a dedicated experiment CA/certificates; the server leaf must contain exactly
the generated arm's target SPIFFE ID. The real mTLS probe verifies that identity,
chain and current certificate serial; NGINX verifies the private key. Static
credentials are neither generated from production secrets nor included in the
artifact archive. Static `start/status/verify/stop` use the generated lifecycle
extension and start only AuthZ/NGINX. Helper faults, SVID rotation and Node join
experiments are `NOT_APPLICABLE` for the static cost-reference arm.

The client side must also use dedicated static client certificates from this
experiment CA, with the exact generated allowed IDs. Do not combine a static
server CA with unconfigured SPIRE-issued clients and call that static mTLS.
`static_clients.py` builds an experiment-only Linux credential publisher using
a Go overlay; **no production source file or binary gains a static-mode flag**:

```sh
python3 experiments/argus/static_clients.py build --output /root/static-client-build
```

Stage the resulting binary in a protected executable path on the client Guest,
pin its digest, and supply a protected config like:

```json
{
  "schema": "argus.static-clients.v1",
  "fleet_config": "/etc/argus-experiment/static-fleet.json",
  "binary": "/opt/argus-experiment/argus-static-client",
  "binary_sha256": "<exact build digest>",
  "instances": {
    "static-a": {"cert":"/etc/argus-test-ca/a.pem","key":"/etc/argus-test-ca/a-key.pem","bundle":"/etc/argus-test-ca/bundle.pem"}
  }
}
```

Install the isolated static-arm Gateway with the ordinary fleet renderer and
installer first, keeping its publisher stopped. `static_clients.py install
--config ... --instance static-a` records a backup of that instance's dynamic
publisher unit and installs its static counterpart. Start the Gateway, then use
`static_clients.py register --config ... --instance static-a --pid <actual-pid>`
instead of normal `guest-register`. `inspect` verifies the loaded unit/binary;
`restore` stops it, removes its registration, and restores only its original unit.

The static publisher validates the dedicated certificate chain, exact SPIFFE ID,
key match and expiration using the same SVID validator. It publishes through the
same generation/lease format, private reader group and tmpfs directory, and
renews a 2.5-second lease only after a completed PID/starttime/namespace check.
It never contacts the Workload API or Broker. Static certificate rotation is not
performed. The local client lease guard remains shared across comparison arms;
report this explicitly rather than describing the static arm as an unguarded
client. Existing shared-node services can remain available for other clients;
their CPU cost must not be attributed to this arm's static publisher.

## Evidence boundary

`inspect` returning PASS means the artifact/configuration matches the intended
arm. It is not an attestation, systemd failure experiment or business result.
Rendering leaves `remote_acceptance=NOT_RUN`. Record the actual live inspection,
client/receiver data and measured outcomes separately. The native baseline and
two ablations do not establish that a second Quote is indispensable, and no
comparison against an application-inline implementation is provided.
