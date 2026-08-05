"""Marketplace registry — SQLite-backed index of listings shared across the global mesh.

Each listing:
  - kind:        mcp_server | knowledge | idea
  - domain:      capability domain (crypto, reverse, algo, embedded, ...)
  - origin:      originating OpenCode instance (instance id)
  - owner:       publishing agent id (ECDSA public key hex, when signed)
  - title, description, payload (JSON), tags
  - created_at, ttl_seconds (expiry)

Publish flow:  registry.publish() -> store locally + enqueue for mesh broadcast
Pull flow:     registry.ingest_mesh() -> accept listings received from other instances
"""

import json
import os
import sqlite3
import threading
import time
import uuid
import hashlib
from pathlib import Path

DATA_DIR = Path(os.environ.get("SINT_MARKETPLACE_DIR", Path.home() / ".config" / "opencode" / "marketplace"))
DB_FILE = DATA_DIR / "registry.db"
KINDS = ("mcp_server", "knowledge", "idea")


class Registry:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_FILE
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._init_db()

    # ── schema ─────────────────────────────────────────────
    def _init_db(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    domain TEXT NOT NULL DEFAULT '',
                    origin TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    tags TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    ttl_seconds INTEGER NOT NULL DEFAULT 86400,
                    mesh_hop INTEGER NOT NULL DEFAULT 0,
                    signature TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_kind ON listings(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_domain ON listings(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_created ON listings(created_at)")
            conn.commit()

    # ── identity ───────────────────────────────────────────
    @staticmethod
    def instance_id() -> str:
        """Stable instance id (per install)."""
        id_file = DATA_DIR / "instance_id"
        if id_file.exists():
            return id_file.read_text().strip()
        iid = hashlib.sha256(f"opencode-{uuid.uuid4()}".encode()).hexdigest()[:16]
        id_file.parent.mkdir(parents=True, exist_ok=True)
        id_file.write_text(iid)
        return iid

    # ── publish ────────────────────────────────────────────
    def publish(
        self,
        kind: str,
        title: str,
        description: str = "",
        domain: str = "",
        payload: dict | None = None,
        tags: list[str] | None = None,
        owner: str = "",
        ttl_seconds: int = 86400,
    ) -> dict:
        """Create a listing locally and stage it for mesh broadcast.

        Returns the listing dict including its id.
        """
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
        listing_id = hashlib.sha256(
            f"{kind}:{domain}:{title}:{time.time()}".encode()
        ).hexdigest()[:16]
        listing = {
            "id": listing_id,
            "kind": kind,
            "domain": domain,
            "origin": self.instance_id(),
            "owner": owner,
            "title": title,
            "description": description,
            "payload": payload or {},
            "tags": sorted(set(tags or [])),
            "created_at": time.time(),
            "ttl_seconds": ttl_seconds,
            "mesh_hop": 0,
            "signature": "",
        }
        with self.lock:
            self._upsert(listing)
            self._enqueue_mesh(listing)
        return listing

    def _upsert(self, listing: dict):
        listing = dict(listing)  # defensive copy
        defaults = {
            "owner": "", "description": "", "tags": [], "payload": {},
            "mesh_hop": 0, "signature": "",
        }
        for k, v in defaults.items():
            listing.setdefault(k, v)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO listings
                   (id, kind, domain, origin, owner, title, description, payload,
                    tags, created_at, ttl_seconds, mesh_hop, signature)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    listing["id"], listing["kind"], listing["domain"], listing["origin"],
                    listing["owner"], listing["title"], listing["description"],
                    json.dumps(listing["payload"], ensure_ascii=False),
                    json.dumps(listing.get("tags", []), ensure_ascii=False),
                    listing["created_at"], listing["ttl_seconds"],
                    listing.get("mesh_hop", 0), listing.get("signature", ""),
                ),
            )
            conn.commit()

    # ── mesh outbox ────────────────────────────────────────
    def _enqueue_mesh(self, listing: dict):
        """Append listing to outbox file for the mesh broadcaster."""
        outbox = DATA_DIR / "outbox.jsonl"
        with outbox.open("a") as f:
            f.write(json.dumps(listing, ensure_ascii=False) + "\n")

    def outbox(self) -> list[dict]:
        """Read pending mesh-out listings (does not clear)."""
        outbox = DATA_DIR / "outbox.jsonl"
        if not outbox.exists():
            return []
        return [json.loads(line) for line in outbox.read_text().splitlines() if line.strip()]

    def clear_outbox(self, ids: list[str] | None = None):
        outbox = DATA_DIR / "outbox.jsonl"
        if not outbox.exists():
            return
        lines = [json.loads(l) for l in outbox.read_text().splitlines() if l.strip()]
        keep = [l for l in lines if ids is not None and l["id"] not in ids]
        outbox.write_text("\n".join(json.dumps(l) for l in keep) + ("\n" if keep else ""))

    # ── ingest from peers ──────────────────────────────────
    def ingest_mesh(self, listing: dict, mesh_hop: int = 1) -> str:
        """Accept a listing received from another instance.

        Returns 'stored' | 'duplicate' | 'expired' | 'invalid'.
        """
        kind = listing.get("kind")
        if kind not in KINDS:
            return "invalid"
        listing_id = listing.get("id") or hashlib.sha256(
            f"{kind}:{listing.get('domain','')}:{listing.get('title','')}:{listing.get('created_at',0)}".encode()
        ).hexdigest()[:16]
        created = float(listing.get("created_at", 0))
        ttl = int(listing.get("ttl_seconds", 86400))
        if time.time() - created > ttl:
            return "expired"
        listing.setdefault("mesh_hop", mesh_hop)
        listing["id"] = listing_id
        with self.lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                row = conn.execute("SELECT 1 FROM listings WHERE id=?", (listing_id,)).fetchone()
                if row:
                    return "duplicate"
            self._upsert(listing)
        return "stored"

    # ── search ─────────────────────────────────────────────
    def search(
        self,
        query: str = "",
        kind: str | None = None,
        domain: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search listings by keyword, kind, domain, tags."""
        if kind is not None and kind not in KINDS:
            return []
        now = time.time()
        results = []
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            sql = "SELECT * FROM listings WHERE created_at > ?"
            params: list = [now - 9999999999]
            # filter expired
            sql = "SELECT * FROM listings"
            where = []
            wparams: list = []
            if kind:
                where.append("kind = ?")
                wparams.append(kind)
            if domain:
                where.append("domain = ?")
                wparams.append(domain)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY created_at DESC LIMIT ?"
            wparams.append(limit * 4)
            rows = conn.execute(sql, wparams).fetchall()

        q = query.strip().lower()
        for row in rows:
            rec = dict(row)
            if time.time() - rec["created_at"] > rec["ttl_seconds"]:
                continue
            rec["payload"] = json.loads(rec["payload"] or "{}")
            rec["tags"] = json.loads(rec["tags"] or "[]")
            if tags:
                if not set(tags) & set(rec["tags"]):
                    continue
            if q:
                haystack = " ".join([
                    rec["title"], rec["description"], rec["domain"],
                    " ".join(rec["tags"]), json.dumps(rec["payload"]),
                ]).lower()
                if q not in haystack:
                    continue
            results.append(rec)
        return results[:limit]

    def get(self, listing_id: str) -> dict | None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        if not row:
            return None
        rec = dict(row)
        rec["payload"] = json.loads(rec["payload"] or "{}")
        rec["tags"] = json.loads(rec["tags"] or "[]")
        return rec

    # ── stats ──────────────────────────────────────────────
    def stats(self) -> dict:
        with sqlite3.connect(str(self.db_path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            by_kind = conn.execute(
                "SELECT kind, COUNT(*) c FROM listings GROUP BY kind ORDER BY c DESC"
            ).fetchall()
            by_domain = conn.execute(
                "SELECT domain, COUNT(*) c FROM listings WHERE domain != '' GROUP BY domain ORDER BY c DESC LIMIT 10"
            ).fetchall()
            peers = conn.execute(
                "SELECT DISTINCT origin FROM listings"
            ).fetchall()
        return {
            "total_listings": total,
            "by_kind": [{"kind": r[0], "count": r[1]} for r in by_kind],
            "by_domain": [{"domain": r[0], "count": r[1]} for r in by_domain],
            "peer_instances": [r[0] for r in peers],
            "instance_id": self.instance_id(),
            "db": str(self.db_path),
        }

    def forget_expired(self) -> int:
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                "DELETE FROM listings WHERE created_at + ttl_seconds < ?", (time.time(),)
            )
            conn.commit()
            return cur.rowcount
