"""ZK Proofs — re-export shim from aimarket_hub.zk_proofs (canonical).

The hub-core module is the single source of truth. This plugin-level module
exists for the plugin entry_point manifest; its logic delegates to hub-core.
"""

from aimarket_hub.zk_proofs import *  # noqa: F401, F403
from aimarket_hub.zk_proofs import ZKProverSimulated as ZKProver  # noqa: F401 — compat alias
