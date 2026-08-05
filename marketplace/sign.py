"""ECDSA signing for marketplace listings.

Each listing can be signed by its publisher so the mesh knows WHO
published WHAT. Verification happens on ingest — unsigned listings are
kept (MVP) but marked; signed ones carry provenance.

Implementation is self-contained (does not depend on acp-mvp): same
SECP256R1 scheme as ACP identity, so signatures are cross-verifiable.
"""

import hashlib
import json
import logging
import os
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger("marketplace.sign")

DATA_DIR = Path(os.environ.get("SINT_MARKETPLACE_DIR", Path.home() / ".config" / "opencode" / "marketplace"))
KEYSTORE = DATA_DIR / "keystore.json"
DEFAULT_PASSWORD = os.environ.get("ACP_AGENT_PASSWORD", "change-me")


def _canonical(listing: dict) -> bytes:
    """Canonical serialization for signing: stable field order.

    Signature covers only the CONTENT fields. Identity fields (owner,
    signature, signature_valid) are attached separately so that
    sign-time and verify-time payloads always match.
    """
    listing = dict(listing)
    listing.pop("signature", None)
    listing.pop("owner", None)
    listing.pop("signature_valid", None)
    listing.pop("mesh_hop", None)  # routing counter — not part of signed content
    return json.dumps(listing, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _derive_key(salt: bytes, password: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt,
        iterations=100_000, backend=default_backend(),
    )
    return kdf.derive(password.encode("utf-8"))


def ensure_identity(password: str = DEFAULT_PASSWORD) -> dict:
    """Generate a keypair if missing, else load. Returns {agent_id, public_key}."""
    if KEYSTORE.exists():
        return load_identity(password)
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    public_key = private_key.public_key()
    pn = public_key.public_numbers()
    public_bytes = b"\x04" + pn.x.to_bytes(32, "big") + pn.y.to_bytes(32, "big")
    agent_id = "0x" + public_bytes.hex()

    private_der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    salt = os.urandom(16)
    nonce = os.urandom(12)
    aes_key = _derive_key(salt, password)
    ciphertext = AESGCM(aes_key).encrypt(nonce, private_der, None)

    KEYSTORE.parent.mkdir(parents=True, exist_ok=True)
    KEYSTORE.write_text(json.dumps({
        "agent_id": agent_id,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
        "public_key": public_bytes.hex(),
    }, indent=2))
    os.chmod(KEYSTORE, 0o600)
    logger.info("marketplace identity created: %s...", agent_id[:18])
    return {
        "agent_id": agent_id,
        "public_key": public_bytes.hex(),
        "private_key": private_key,
    }


def load_identity(password: str = DEFAULT_PASSWORD) -> dict | None:
    if not KEYSTORE.exists():
        return None
    data = json.loads(KEYSTORE.read_text())
    aes_key = _derive_key(bytes.fromhex(data["salt"]), password)
    private_bytes = AESGCM(aes_key).decrypt(
        bytes.fromhex(data["nonce"]), bytes.fromhex(data["ciphertext"]), None
    )
    return {
        "agent_id": data["agent_id"],
        "public_key": data["public_key"],
        "private_key": serialization.load_der_private_key(
            private_bytes, password=None, backend=default_backend()
        ),
    }


def sign_listing(listing: dict, password: str = DEFAULT_PASSWORD) -> dict:
    """Sign a listing, filling owner + signature. Returns the same dict."""
    identity = ensure_identity(password)
    payload = _canonical(listing)
    sig = identity["private_key"].sign(payload, ec.ECDSA(hashes.SHA256()))
    listing["owner"] = identity["agent_id"]
    listing["signature"] = sig.hex()
    return listing


def verify_listing(listing: dict) -> bool:
    """Verify a listing's signature against its owner public key."""
    sig_hex = listing.get("signature", "")
    owner = listing.get("owner", "")
    if not sig_hex or not owner:
        return False
    try:
        public_bytes = bytes.fromhex(owner[2:] if owner.startswith("0x") else owner)
        if len(public_bytes) != 65 or public_bytes[0] != 0x04:
            return False
        x = int.from_bytes(public_bytes[1:33], "big")
        y = int.from_bytes(public_bytes[33:65], "big")
        pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key(default_backend())
        pub.verify(bytes.fromhex(sig_hex), _canonical(listing), ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:  # noqa: BLE001
        return False
