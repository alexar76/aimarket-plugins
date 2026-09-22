"""Cross-check gateway tool routing against live oracle capability specs."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from aimarket_oracle_gateway.gateway_core import CAPABILITIES

REPO_ROOT = Path(__file__).resolve().parents[3]
ORACLES_ROOT = REPO_ROOT / "oracles" / "oracles"
ORACLE_CORE_ROOT = REPO_ROOT / "oracles" / "core"
PLATON_BACKEND = ORACLES_ROOT / "platon" / "backend"

ORACLE_MODULES = [
    "chronos",
    "lattice",
    "murmuration",
    "lumen",
    "colony",
    "turing",
    "percola",
    "fermat",
    "ablation",
    "landauer",
    "sortes",
    "gauss",
    "aestus",
    "betti",
    "kantor",
    "fourier",
]


def _ensure_paths() -> None:
    for p in (ORACLE_CORE_ROOT, PLATON_BACKEND):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    for name in ORACLE_MODULES:
        pkg_root = ORACLES_ROOT / name
        s = str(pkg_root)
        if s not in sys.path:
            sys.path.insert(0, s)


def _collect_oracle_core_ids() -> set[str]:
    _ensure_paths()
    ids: set[str] = set()
    for name in ORACLE_MODULES:
        mod = importlib.import_module(f"{name}.capabilities")
        spec = getattr(mod, "SPEC")
        for cap in spec.capabilities:
            ids.add(cap.capability_id)
    return ids


def _collect_platon_ids() -> set[str]:
    _ensure_paths()
    from platon.aimarket import CAPABILITIES

    return {c["capability_id"] for c in CAPABILITIES}


@pytest.fixture(scope="module")
def live_capability_ids() -> set[str]:
    if not ORACLES_ROOT.is_dir():
        pytest.skip("oracles tree not present (standalone satellite clone)")
    return _collect_oracle_core_ids() | _collect_platon_ids()


def test_every_gateway_capability_exists_in_oracles(live_capability_ids):
    missing = []
    for tool, spec in CAPABILITIES.items():
        if spec.capability_id not in live_capability_ids:
            missing.append(f"{tool} -> {spec.capability_id}")
    assert not missing, "Gateway routes to unknown capabilities:\n" + "\n".join(missing)
