"""Minimal Web Push sender: VAPID (RFC 8292) + aes128gcm payload encryption (RFC 8291/8188).

Implemented with the `cryptography` package only, so there is no extra dependency
to install on Windows. Returns the HTTP status from the push service.
"""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096


def b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64u_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _public_bytes(key) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def generate_vapid_keys() -> tuple[str, str]:
    """Returns (public, private) as base64url strings for the VAPID_* settings."""
    private = ec.generate_private_key(ec.SECP256R1())
    raw_private = private.private_numbers().private_value.to_bytes(32, "big")
    return b64u_encode(_public_bytes(private.public_key())), b64u_encode(raw_private)


def _load_private(private_b64: str):
    return ec.derive_private_key(int.from_bytes(b64u_decode(private_b64), "big"), ec.SECP256R1())


def vapid_header(endpoint: str, public_b64: str, private_b64: str, subject: str) -> str:
    url = urlparse(endpoint)
    header = b64u_encode(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u_encode(json.dumps(
        {"aud": f"{url.scheme}://{url.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject},
        separators=(",", ":"),
    ).encode())
    signing_input = f"{header}.{claims}".encode()
    der = _load_private(private_b64).sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = b64u_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{signature}, k={public_b64}"


def encrypt(payload: bytes, p256dh_b64: str, auth_b64: str, *, salt: bytes = None, sender_key=None) -> bytes:
    """aes128gcm-encrypt `payload` for one subscription (single record)."""
    ua_public = b64u_decode(p256dh_b64)
    auth_secret = b64u_decode(auth_b64)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(sender_key.public_key())
    shared = sender_key.exchange(ec.ECDH(), ua_key)

    ikm = HKDF(hashes.SHA256(), 32, salt=auth_secret, info=b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


def send(subscription, data: dict, *, public_b64, private_b64, subject, ttl=24 * 3600, urgency="normal") -> int:
    body = encrypt(json.dumps(data).encode(), subscription.p256dh, subscription.auth)
    request = urllib.request.Request(subscription.endpoint, data=body, method="POST", headers={
        "Authorization": vapid_header(subscription.endpoint, public_b64, private_b64, subject),
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": str(ttl), "Urgency": urgency,
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
