"""Mesh transport for the global marketplace.

Broadcasts marketplace listings between OpenCode instances over the same
SMTP/EML fabric that ACP contracts use:

  To: marketplace.{kind}@mesh.local

Two directions:
  broadcast(listing)  — send a listing to the mesh via SMTP relay
  ingest_eml(eml)     — parse an inbound marketplace message and store it
"""

import json
import logging
import os
import smtplib
import time
import uuid
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path

logger = logging.getLogger("marketplace.mesh")

SMTP_RELAY = os.environ.get("ACP_SMTP_RELAY", "localhost")
SMTP_PORT = int(os.environ.get("ACP_RELAY_PORT", "2525"))
MESH_RECIPIENT = "marketplace.{kind}@mesh.local"


def build_eml(listing: dict, agent_id: str = "marketplace") -> str:
    """Serialize a listing into a marketplace .eml message."""
    msg = MIMEText(
        json.dumps(listing, indent=2, ensure_ascii=False),
        "plain", "utf-8",
    )
    msg["Message-ID"] = f"<{uuid.uuid4()}@mesh.local>"
    msg["From"] = f"{agent_id}@mesh.local"
    msg["To"] = MESH_RECIPIENT.format(kind=listing.get("kind", "knowledge"))
    msg["Subject"] = f"[MARKETPLACE] {listing.get('kind','')}: {listing.get('title','')}"
    msg["X-Agent-Protocol"] = "ACP/0.1"
    msg["X-Marketplace-Kind"] = listing.get("kind", "")
    msg["X-Marketplace-Domain"] = listing.get("domain", "")
    msg["X-Marketplace-Origin"] = listing.get("origin", "")
    msg["X-Proof-of-Stake"] = "null"
    msg["Date"] = formatdate(localtime=True)
    return msg.as_string()


def broadcast(listing: dict, relay: str = SMTP_RELAY, port: int = SMTP_PORT) -> dict:
    """Send a listing to the mesh via the SMTP relay.

    Returns {'status': 'sent'|'queued'|'failed', 'detail': ...}
    If the relay is unreachable, the listing stays in the outbox and
    the caller may retry later (mesh is asynchronous).
    """
    eml_content = build_eml(listing)
    try:
        with smtplib.SMTP(relay, port, timeout=5) as smtp:
            smtp.sendmail(
                f"{listing.get('owner') or 'marketplace'}@mesh.local",
                MESH_RECIPIENT.format(kind=listing.get("kind", "knowledge")),
                eml_content,
            )
        return {"status": "sent", "to": MESH_RECIPIENT.format(kind=listing.get("kind", ""))}
    except Exception as e:  # noqa: BLE001
        logger.debug("Broadcast failed (queued for retry): %s", e)
        return {"status": "queued", "detail": str(e)}


def ingest_eml(eml_text: str, registry) -> dict:
    """Parse a raw marketplace .eml and ingest into the registry.

    Returns {'status': 'stored'|'duplicate'|'expired'|'invalid'|'not_marketplace', ...}
    """
    # Use the email stdlib to decode MIME properly (base64 bodies included).
    from email import policy
    from email.parser import BytesParser
    try:
        msg = BytesParser(policy=policy.default).parsebytes(
            eml_text.encode("utf-8") if isinstance(eml_text, str) else eml_text
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "invalid", "detail": f"mime parse: {e}"}

    headers = {k.lower(): v for k, v in msg.items()}
    if headers.get("x-marketplace-kind") is None:
        return {"status": "not_marketplace"}

    body = msg.get_payload(decode=True)
    if body is None:
        body = str(msg.get_payload() or "").encode("utf-8", errors="replace")
    try:
        listing = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return {"status": "invalid", "detail": f"body is not JSON: {e}"}

    listing.setdefault("kind", headers.get("x-marketplace-kind"))
    listing.setdefault("domain", headers.get("x-marketplace-domain", ""))
    listing.setdefault("origin", headers.get("x-marketplace-origin", ""))
    status = registry.ingest_mesh(listing, mesh_hop=1)
    return {"status": status}


def ingest_spool(spool_dir: str | Path, registry) -> dict:
    """Ingest all marketplace .eml files from a spool directory."""
    spool = Path(spool_dir)
    if not spool.exists():
        return {"status": "no_spool", "ingested": 0, "skipped": 0}
    ingested = 0
    skipped = 0
    for eml_file in spool.glob("*.eml"):
        try:
            result = ingest_eml(eml_file.read_text(encoding="utf-8", errors="replace"), registry)
        except Exception:  # noqa: BLE001
            result = {"status": "error"}
        if result["status"] in ("stored", "duplicate"):
            ingested += 1
        else:
            skipped += 1
        # process each file once
        eml_file.rename(eml_file.with_suffix(".processed"))
    return {"status": "ok", "ingested": ingested, "skipped": skipped}
