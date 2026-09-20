"""Local Fulcio-shaped PKI with a cryptographically valid embedded SCT.

This generates test authorities, not production Fulcio credentials. Verification
uses the real Sigstore certificate/CT/time implementation without mocks.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import struct

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID, ObjectIdentifier


def b64(data):
    return base64.b64encode(data).decode("ascii")


def spki(key):
    return key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def certificate_material(directory, rekor_key, *, bad_sct=False, missing_sct=False, expired=False, untrusted_ca=False):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ca_key, ct_key, signer = [ec.generate_private_key(ec.SECP256R1()) for _ in range(3)]
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Fulcio CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=4)).not_valid_after(now + timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    cert_time = now - timedelta(days=2) if expired else now
    identity, issuer = "recorder@example.test", "https://issuer.example.test"
    builder = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test recorder")]))
               .issuer_name(ca_name).public_key(signer.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(cert_time - timedelta(minutes=5)).not_valid_after(cert_time + timedelta(minutes=5))
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, None, None), critical=True)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=False)
               .add_extension(x509.SubjectAlternativeName([x509.RFC822Name(identity)]), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
               .add_extension(x509.UnrecognizedExtension(ObjectIdentifier("1.3.6.1.4.1.57264.1.1"), issuer.encode()), critical=False))
    # RFC 6962 precertificate signed-entry: timestamp + issuer SPKI hash + TBS.
    precert = builder.sign(ca_key, hashes.SHA256())
    tbs = precert.tbs_certificate_bytes
    timestamp = int(cert_time.timestamp() * 1000)
    signed = (struct.pack("!BBQH", 0, 0, timestamp, 1) + hashlib.sha256(spki(ca_key)).digest()
              + len(tbs).to_bytes(3, "big") + tbs + b"\x00\x00")
    signature = ct_key.sign(signed, ec.ECDSA(hashes.SHA256()))
    if bad_sct:
        signature = signature[:-1] + bytes([signature[-1] ^ 1])
    sct = (b"\x00" + hashlib.sha256(spki(ct_key)).digest() + struct.pack("!Q", timestamp)
           + b"\x00\x00\x04\x03" + len(signature).to_bytes(2, "big") + signature)
    sct_list = (len(sct) + 2).to_bytes(2, "big") + len(sct).to_bytes(2, "big") + sct
    length = len(sct_list)
    der_length = bytes([length]) if length < 128 else b"\x81" + bytes([length])
    if not missing_sct:
        builder = builder.add_extension(x509.UnrecognizedExtension(
            ExtensionOID.PRECERT_SIGNED_CERTIFICATE_TIMESTAMPS, b"\x04" + der_length + sct_list), critical=False)
    cert = builder.sign(ca_key, hashes.SHA256())

    def log(key, details):
        return {"baseUrl": "https://log.example.test", "hashAlgorithm": "SHA2_256",
                "publicKey": {"rawBytes": b64(spki(key)), "keyDetails": details},
                "logId": {"keyId": b64(hashlib.sha256(spki(key)).digest())}}

    root_cert = ca
    if untrusted_ca:
        other = ec.generate_private_key(ec.SECP256R1())
        root_cert = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
                     .public_key(other.public_key()).serial_number(x509.random_serial_number())
                     .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                     .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                     .sign(other, hashes.SHA256()))
    root = {"mediaType": "application/vnd.dev.sigstore.trustedroot+json;version=0.1",
            "tlogs": [log(rekor_key, "PKIX_ECDSA_P384_SHA_384")],
            "ctlogs": [log(ct_key, "PKIX_ECDSA_P256_SHA_256")],
            "certificateAuthorities": [{"uri": "https://fulcio.example.test", "certChain": {
                "certificates": [{"rawBytes": b64(root_cert.public_bytes(serialization.Encoding.DER))}]}}]}
    path = directory / "trusted_root.json"
    path.write_text(json.dumps(root))
    return signer, cert.public_bytes(serialization.Encoding.PEM), {
        "sigstore_trusted_root_path": str(path), "signer_identity": identity, "signer_issuer": issuer}
