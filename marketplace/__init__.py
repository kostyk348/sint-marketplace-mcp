"""sint-marketplace-mcp — global mesh marketplace for OpenCode.

Publishes and discovers three kinds of listings between OpenCode instances
over the ACP mesh network (SMTP/EML transport, same as agent contracts):

  - mcp_server : a reusable MCP server / tool-set capability offered by an instance
  - knowledge  : a verified FACT block, pattern, or snippet (from sint-ua / forest)
  - idea       : a hypothesis, belief, or proposed concept (from liquid / patrimonio)

Listings are broadcast via the ACP addressing scheme:
  To: marketplace.{kind}@mesh.local   (or broadcast+marketplace.{kind}@mesh.local)
"""

from .index import Registry

_registry = Registry()


def get_registry() -> Registry:
    """Return the process-wide marketplace registry singleton."""
    return _registry