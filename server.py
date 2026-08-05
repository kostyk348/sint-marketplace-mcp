"""sint-marketplace-mcp — global mesh marketplace for OpenCode.

Publishes, discovers and synchronizes listings (MCP servers, knowledge, ideas)
between OpenCode instances over the ACP mesh fabric (SMTP/EML).

Run:
  python3 server.py            (stdio transport, MCP)
"""

from fastmcp import FastMCP
from config import NAME, VERSION
from marketplace import get_registry
from marketplace.mesh import broadcast, ingest_spool, build_eml

mcp = FastMCP(NAME)


@mcp.tool()
def marketplace_publish(
    kind: str,
    title: str,
    description: str = "",
    domain: str = "",
    payload: dict | None = None,
    tags: list[str] | None = None,
    owner: str = "",
    ttl_seconds: int = 86400,
    broadcast_to_mesh: bool = True,
) -> dict:
    """Publish a listing to the global marketplace.

    kind: 'mcp_server' | 'knowledge' | 'idea'
    - mcp_server : offer a reusable MCP server / tool-set capability
    - knowledge  : share a verified FACT block / pattern / snippet
    - idea       : share a hypothesis or proposed concept

    Stores locally, then broadcasts over the mesh (SMTP relay) so other
    OpenCode instances can discover it.
    """
    listing = get_registry().publish(
        kind=kind, title=title, description=description, domain=domain,
        payload=payload, tags=tags, owner=owner, ttl_seconds=ttl_seconds,
    )
    mesh = broadcast(listing) if broadcast_to_mesh else {"status": "skipped"}
    return {"listing": listing, "mesh": mesh}


@mcp.tool()
def marketplace_search(
    query: str = "",
    kind: str | None = None,
    domain: str | None = None,
    tags: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search the global marketplace for listings.

    filter by: kind (mcp_server|knowledge|idea), domain (crypto, reverse,
    algo, embedded, ...), tags, and free-text query.
    """
    return get_registry().search(query=query, kind=kind, domain=domain, tags=tags, limit=limit)


@mcp.tool()
def marketplace_subscribe(kind: str, domain: str = "", tags: list[str] | None = None) -> dict:
    """Register a subscription filter.

    For MVP this persists the filter locally and is honored by
    marketplace_pull; production wiring to the relay fan-out is next.
    """
    reg = get_registry()
    sub_id = reg.publish(
        kind="idea",  # subscriptions stored as meta-listings
        title=f"subscription:{kind}:{domain}",
        description=json.dumps({"subscribe_kind": kind, "domain": domain, "tags": tags or []}),
        domain=domain,
        tags=tags or [],
        owner="subscription",
    )
    return {"status": "subscribed", "subscription_id": sub_id["id"], "filter": {"kind": kind, "domain": domain, "tags": tags}}


@mcp.tool()
def marketplace_pull(spool_dir: str = "") -> dict:
    """Pull new marketplace listings from the inbound mesh spool.

    Spool dir defaults to env ACP_INBOX or ./spool/executor.
    Processes each *.eml as a marketplace message (X-Marketplace-Kind header).
    """
    import os
    from marketplace.index import get_registry
    spool = spool_dir or os.environ.get("ACP_INBOX", "spool/executor")
    return ingest_spool(spool, get_registry())


@mcp.tool()
def marketplace_outbox() -> list[dict]:
    """List listings queued for mesh broadcast (unsent)."""
    return get_registry().outbox()


@mcp.tool()
def marketplace_export() -> str:
    """Export the full registry as EML bundle (one listing per message)."""
    reg = get_registry()
    listings = reg.search(limit=1000)
    return "\n\n".join(build_eml(l) for l in listings)


@mcp.tool()
def marketplace_stats() -> dict:
    """Marketplace statistics: totals, kinds, domains, peer instances."""
    return get_registry().stats()


# ensure json imported for subscribe description serialization
import json  # noqa: E402


if __name__ == "__main__":
    mcp.run(transport="stdio")
