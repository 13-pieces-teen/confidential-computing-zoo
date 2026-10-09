# IP1 -> IP2 live-gate fix + fresh bundle 2026-10-08

status: LIVE_GATE_FIXED_AND_FRESH_BUNDLE
fixed_at: 2026-10-08T00:54Z (+08:00 08:54)

## 1. Root cause of the stale gate

authorized_keys line 3 forced command had the paper02 script path but the
--config argument still pointed at paper01:

    .../paper02/full_argus/payload/scripts/workload.py server-check     --config /etc/argus-experiments/paper01/full_argus/environment.json

The gate read the paper01 config, looked up the paper01 helper Entry
(removed in the paper02 entry set), and returned
WORKLOAD_ATTESTATION=FAIL: no Entry for
spiffe://argus.local/infra/openviking-helper/experiment/paper01/full

## 2. Fix now live

Line 3 full forced command is now:

    restrict,from="127.0.0.1",command="/usr/bin/python3 /opt/argus-experiments/paper02/full_argus/payload/scripts/workload.py server-check --config /etc/argus-experiments/paper02/full_argus/environment.json"

(comment updated to argus-ip2-server-check-experiment-paper02full;
backup /root/.ssh/authorized_keys.before-20261008T-fix2; sshd -t OK)

## 3. Local verification of the exact forced command

Executed as root with forced-command environment
(env -i HOME=/root PATH=/usr/bin:/bin SSH_ORIGINAL_COMMAND=''):

    EXIT=0
    server_version=1.15.3, server_pid=2027950
    helper fadd18c2 (rev1) unix:sha256 c62a4495...
    target 7d2f3367 policy argus-workload-openviking-paper02-20261004-01
    config_digest 316ad51d, image_config_digest 321458f0

Expected on IP2 retry with the same line-3 key: contract JSON, exit 0,
no WORKLOAD_ATTESTATION=FAIL.

## 4. Fresh bundle (CA rotated since the 16:35Z package)

The bundle shipped at 16:35Z (sha a72dfbe2) ends at CA EA:6E:5E:4D which
expires 2026-10-08 04:38:50Z (12:38:50 +08:00). The server rotated at
16:38Z; the current CA is now:

    fingerprint SHA256 C5:21:13:CF:5F:C5:B8:6E:C5:05:EC:C6:82:B7:02:EE:9E:82:6B:DB:F0:2E:DD:68:E8:B7:2B:79:B7:E9:1F:7F
    valid 2026-10-07T16:38:40Z -> 2026-10-08T16:38:50Z (2026-10-09 00:38:50 +08:00)

Use bundle-now-20261008T0054Z.pem (sha e24d14780a31c5a95d1e0848a5724efd85e91aca9e4e598664baf9bf73d34b41, 4 CAs including the
current one) to replace /etc/spire/argus-poc/bootstrap.crt. The old
a72dfbe2 bundle is superseded.

## 5. Remaining IP2 sequence

1. Replace bootstrap.crt with bundle-now-20261008T0054Z.pem.
2. Restore Provider/Agent -> Node re-attestation under policy
   argus-workload-openviking-paper02-20261004-01 (RTMR2 f1ac9de1...).
3. Verify node re-entry, then start helper / business entry / 1943.
4. Signal IP1 to rerun the three-leg probe with current credentials.

native line 4 untouched.
