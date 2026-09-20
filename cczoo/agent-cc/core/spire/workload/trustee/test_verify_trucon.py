"""Cryptographic fixtures, not a mocked verification verdict or hardware Quote."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from tlog.digest import canonical_json, compute_entry_digest, compute_event_digest
from verify_trucon import AUTH_FIELDS, LogVerifier, strict_json
from certificate_fixtures import certificate_material


def b64(data):
    return base64.b64encode(data).decode("ascii")


def pem(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def encode(value):
    return canonical_json(value).encode("utf-8")


def fixture(tmp_path, *, launch_result="success", wrong_owner=False, baseline="0" * 96, gap=False,
            wrong_link=False, unicode_label=False, digest_error=False, bad_dsse=False, stop=False, virtual_index=False,
            fulcio=False, certificate_options=None, stop_reference=None, stop_instance=None, stop_operation="stop",
            restart=False):
    log_key, owner, stranger = [ec.generate_private_key(ec.SECP384R1()) for _ in range(3)]
    key_path, init_path = tmp_path / "rekor.pem", tmp_path / "init.pem"
    key_path.write_bytes(pem(log_key))
    init_path.write_bytes(pem(owner))
    config = {"rekor_url": "https://rekor.example", "rekor_public_key_path": str(key_path),
              "init_public_key_paths": [str(init_path)], "allowed_baseline_rtmr": ["0" * 96]}
    if fulcio:
        signer, cert_pem, certificate_config = certificate_material(tmp_path, log_key, **(certificate_options or {}))
        config.update(certificate_config)
        config["init_public_key_paths"] = []
    runtime = json.loads((Path(__file__).resolve().parents[1] / "testdata/runtime-data.json").read_text())["runtime_data"]
    info = {"workload_id": runtime["workload_id"], "launch_id": runtime["launch_id"],
            "container_ID": runtime["container_id"], "container_Status": "running",
            "runtime_image_config_digest": runtime["image_config_digest"]}
    other = {**info, "workload_id": "other-workload", "launch_id": "other-launch", "container_ID": "d" * 64}
    records = [
        ("chain.init", [("baseline_rtmr", baseline), ("pub_key", pem(owner).decode())]),
        ("image_build", [("operation_type", "build"), ("note", "构建镜像" if unicode_label else "build")]),
        ("container_launch", [("operation_type", "launch"), ("launch_result", launch_result), ("container_info", info)]),
        ("container_launch", [("operation_type", "launch"), ("launch_result", "success"), ("container_info", other)]),
    ]
    if stop:
        reference = runtime["container_id"] if stop_reference is None else stop_reference
        records[-1] = ("docker_" + stop_operation, [("operation_type", stop_operation), ("operation_result", "success"),
                          ("container_id", reference)])
        if stop_instance is not None:
            records[-1][1].append(("instance_id", stop_instance))
        if restart:
            records = [records[0], records[2], records[3],
                       ("docker_start", [("operation_type", "start"), ("operation_result", "success"),
                                         ("container_id", runtime["container_id"])])]
    entries, bodies = [], []
    previous_digest = previous_lookup = None
    rtmr = bytes.fromhex(baseline)
    for sequence, (kind, items) in enumerate(records, 1):
        values = [{"key": k, "value": v} for k, v in items]
        digests = [compute_entry_digest(v["key"], v["value"]) for v in values]
        created, event_id = "2026-09-20T00:00:00+00:00", "event-" + str(sequence)
        digest = compute_event_digest(event_id, kind, created, digests)
        p = {"chain_id": "default", "sequence_num": sequence + (1 if gap and sequence == 3 else 0),
             "event_id": event_id, "event_type": kind, "created": created, "entries": values,
             "entry_digests": digests, "digest": digest,
             "prev_event_digest": previous_digest, "prev_lookup_hash": previous_lookup}
        if sequence > 1:
            if wrong_link and sequence == 3:
                p["prev_lookup_hash"] = "sha256:" + "0" * 64
            auth = [p[k] if k != "event_digest" else digest for k in AUTH_FIELDS]
            p["owner_authorization"] = {"algorithm": "ecdsa-p384-sha384", "signed_fields": AUTH_FIELDS,
                "signature": b64((stranger if wrong_owner else owner).sign(encode(list(map(list, zip(AUTH_FIELDS, auth)))), ec.ECDSA(hashes.SHA384())))}
        if digest_error and sequence == 3:
            p["entry_digests"][0] = "sha384:" + "0" * 96
        statement = {"_type": "https://in-toto.io/Statement/v1", "predicateType": "https://trusted-log.dev/v1",
                     "subject": [{"name": "trusted-log-chain_default", "digest": {"sha384": digest[7:]}}], "predicate": p}
        payload = encode(statement)
        kind_bytes = b"application/vnd.in-toto+json"
        certificate_signed = fulcio and sequence in (1, 3)
        signing_key = signer if certificate_signed else owner
        if bad_dsse and sequence == 3:
            signing_key = stranger
        signature = signing_key.sign(b"DSSEv1 %d %s %d " % (len(kind_bytes), kind_bytes, len(payload)) + payload, ec.ECDSA(hashes.SHA256()))
        body = {"kind": "intoto", "apiVersion": "0.0.2", "spec": {"content": {
            "payloadHash": {"algorithm": "sha256", "value": hashlib.sha256(payload).hexdigest()},
            "envelope": {"payloadType": kind_bytes.decode(), "signatures": [{"sig": b64(b64(signature).encode()), "publicKey": b64(cert_pem if certificate_signed else pem(owner))}]}}}}
        bodies.append(encode(body))
        entries.append({"body": b64(bodies[-1]), "attestation": {"data": b64(payload)}, "integratedTime": int(time.time())})
        if sequence > 1 and not kind.endswith("_build"):
            rtmr = hashlib.sha384(rtmr + bytes.fromhex(digest[7:])).digest()
        previous_digest, previous_lookup = digest, "sha256:" + hashlib.sha256(payload).hexdigest()
    leaves = [hashlib.sha256(b"\x00" + b).digest() for b in bodies]
    parent = lambda a, b: hashlib.sha256(b"\x01" + a + b).digest()
    left, right = parent(*leaves[:2]), parent(*leaves[2:])
    root = parent(left, right)
    log_id = hashlib.sha256(log_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).digest()
    note = "rekor.example\n4\n" + b64(root) + "\n"
    checkpoint = note + "\n— rekor.example " + b64(log_id[:4] + log_key.sign(note.encode(), ec.ECDSA(hashes.SHA384()))) + "\n"
    raw = {}
    for index, entry in enumerate(entries):
        entry.update(logIndex=index + (1000000 if virtual_index else 0), logID=log_id.hex())
        set_payload = {k: entry[k] for k in ("body", "integratedTime", "logIndex", "logID")}
        entry["verification"] = {"signedEntryTimestamp": b64(log_key.sign(encode(set_payload), ec.ECDSA(hashes.SHA384()))),
            "inclusionProof": {"logIndex": index, "treeSize": 4, "rootHash": root.hex(),
                "hashes": [leaves[index ^ 1].hex(), (right if index < 2 else left).hex()], "checkpoint": checkpoint}}
        raw[leaves[index].hex()] = entry
    verifier = LogVerifier(config)
    verifier.fetch = lambda uuid: copy.deepcopy(raw[uuid])
    request = {"rekor_entry_uuids": list(raw), "runtime_data": runtime, "rtmr2": rtmr.hex()}
    return verifier, request, raw


def test_target_remains_valid_after_other_workload_extends(tmp_path):
    verifier, request, _ = fixture(tmp_path, unicode_label=True)
    assert verifier.verify(request) == {"verified": True, "baseline_rtmr": "0" * 96,
                                      "launch_entry_uuid": request["rekor_entry_uuids"][2]}


def test_rekor_shard_local_proof_and_global_set_indices(tmp_path):
    verifier, request, _ = fixture(tmp_path, virtual_index=True)
    assert verifier.verify(request)["verified"] is True


def test_intoto_fulcio_certificate_and_owner_key_history(tmp_path):
    from pydantic import TypeAdapter
    from sigstore.models import ProposedEntry
    verifier, request, raw = fixture(tmp_path, fulcio=True, virtual_index=True)
    for entry in raw.values():
        TypeAdapter(ProposedEntry).validate_json(base64.b64decode(entry["body"]))
    assert verifier.verify(request)["verified"] is True


@pytest.mark.parametrize("options", [{"bad_sct": True}, {"missing_sct": True}, {"expired": True}, {"untrusted_ca": True}])
def test_intoto_certificate_checks_cannot_be_skipped(tmp_path, options):
    verifier, request, _ = fixture(tmp_path, fulcio=True, certificate_options=options)
    with pytest.raises(Exception):
        verifier.verify(request)


@pytest.mark.parametrize("field", ["signer_identity", "signer_issuer"])
def test_intoto_certificate_identity_is_pinned(tmp_path, field):
    verifier, request, _ = fixture(tmp_path, fulcio=True)
    verifier.config[field] = "unapproved"
    with pytest.raises(Exception):
        verifier.verify(request)


def test_intoto_certificate_does_not_replace_dsse_signature_verification(tmp_path):
    verifier, request, _ = fixture(tmp_path, fulcio=True, bad_dsse=True)
    with pytest.raises(Exception):
        verifier.verify(request)


@pytest.mark.parametrize("operation", ["stop", "rm"])
@pytest.mark.parametrize("reference", ["a" * 12, "openviking", ""])
def test_ambiguous_legacy_stop_identity_cannot_preserve_old_launch(tmp_path, operation, reference):
    verifier, request, _ = fixture(tmp_path, stop=True, stop_reference=reference, stop_operation=operation, restart=True)
    # A new registration can observe a restarted process with the same labels.
    request["runtime_data"].update(pid="9999", start_time="999999")
    with pytest.raises(ValueError, match="full container ID"):
        verifier.verify(request)


@pytest.mark.parametrize("operation", ["stop", "rm"])
def test_restart_does_not_revalidate_the_old_launch(tmp_path, operation):
    verifier, request, _ = fixture(tmp_path, stop=True, stop_operation=operation, restart=True)
    request["runtime_data"].update(pid="9999", start_time="999999")
    with pytest.raises(ValueError, match="followed by a successful stop/removal"):
        verifier.verify(request)


def test_stop_of_other_container_does_not_invalidate_target(tmp_path):
    verifier, request, _ = fixture(tmp_path, stop=True, stop_reference="d" * 64, stop_instance="d" * 64)
    assert verifier.verify(request)["verified"] is True


def test_conflicting_stop_identity_is_rejected(tmp_path):
    verifier, request, _ = fixture(tmp_path, stop=True, stop_reference="d" * 64, stop_instance="e" * 64)
    with pytest.raises(ValueError, match="conflicting"):
        verifier.verify(request)


@pytest.mark.parametrize("options", [{"launch_result": None}, {"launch_result": "failed"},
    {"wrong_owner": True}, {"baseline": "1" * 96}, {"gap": True}, {"wrong_link": True}, {"digest_error": True},
    {"bad_dsse": True}, {"stop": True}])
def test_signed_but_unacceptable_records(tmp_path, options):
    verifier, request, _ = fixture(tmp_path, **options)
    with pytest.raises(Exception):
        verifier.verify(request)


@pytest.mark.parametrize("field", ["workload_id", "launch_id", "container_id", "image_config_digest"])
def test_current_instance_binding(tmp_path, field):
    verifier, request, _ = fixture(tmp_path)
    request["runtime_data"][field] = "different"
    with pytest.raises(ValueError):
        verifier.verify(request)


@pytest.mark.parametrize("change", ["omit_build", "omit_other_launch", "reorder", "duplicate", "wrong_rtmr", "empty", "client_verdict"])
def test_history_and_request_rejections(tmp_path, change):
    verifier, request, _ = fixture(tmp_path)
    refs = request["rekor_entry_uuids"]
    if change == "omit_build": del refs[1]
    if change == "omit_other_launch": refs.pop()
    if change == "reorder": refs[1], refs[2] = refs[2], refs[1]
    if change == "duplicate": refs.append(refs[0])
    if change == "wrong_rtmr": request["rtmr2"] = "0" * 96
    if change == "empty": request["rekor_entry_uuids"] = []
    if change == "client_verdict": request["verified"] = True
    with pytest.raises(ValueError):
        verifier.verify(request)


@pytest.mark.parametrize("change", ["payload", "missing_payload", "body", "proof", "checkpoint", "unsigned_checkpoint", "timestamp", "missing_entry"])
@pytest.mark.parametrize("fulcio", [False, True])
def test_remote_crypto_material_must_verify(tmp_path, change, fulcio):
    verifier, request, raw = fixture(tmp_path, fulcio=fulcio)
    entry = raw[request["rekor_entry_uuids"][2]]
    if change == "payload": entry["attestation"]["data"] = b64(b"{}")
    if change == "missing_payload": del entry["attestation"]
    if change == "body": entry["body"] = b64(b"{}")
    if change == "proof": entry["verification"]["inclusionProof"]["hashes"][0] = "0" * 64
    if change == "checkpoint": entry["verification"]["inclusionProof"]["checkpoint"] = entry["verification"]["inclusionProof"]["checkpoint"].replace("rekor.example", "other.example")
    if change == "unsigned_checkpoint": entry["verification"]["inclusionProof"]["checkpoint"] = entry["verification"]["inclusionProof"]["checkpoint"].split("\n\n")[0] + "\n\nnot-a-signature\n"
    if change == "timestamp": entry["integratedTime"] += 1
    if change == "missing_entry": del raw[request["rekor_entry_uuids"][2]]
    with pytest.raises(Exception):
        verifier.verify(request)


def test_json_is_unambiguous():
    for value in ('{"verified":true,"verified":false}', '{"value":NaN}'):
        with pytest.raises(ValueError):
            strict_json(value)


@pytest.mark.parametrize("trust", ["initialization", "rekor"])
def test_signed_records_require_pinned_trust(tmp_path, trust):
    verifier, request, _ = fixture(tmp_path)
    if trust == "initialization":
        verifier.init_keys = []
    else:
        from verify_trucon import rekor_keyring
        verifier.keyring = rekor_keyring(pem(ec.generate_private_key(ec.SECP384R1())))
    with pytest.raises(Exception):
        verifier.verify(request)
