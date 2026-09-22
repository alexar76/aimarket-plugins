"""The AWR/2 dependency is declared, loud, and shares the hub's one signing key."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

import aimarket_provenance._awr as binding
from aimarket_provenance._awr import (
    ProvenanceDependencyError,
    ProvenanceKeyError,
    did_key_for_signer,
    signing_key_from_signer,
)

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


class TestDeclaredDependency:
    def test_awr_is_a_declared_dependency(self):
        text = PYPROJECT.read_text()
        block = re.search(r"^dependencies = \[(.*?)^\]", text, re.S | re.M)
        assert block, "no [project].dependencies table"
        requirements = re.findall(r'"([^"]+)"', block.group(1))
        awr_pins = [r for r in requirements if re.match(r"^awr\b", r)]
        assert awr_pins, "awr must be declared in [project].dependencies"
        # §6.4/§3.1: a verifier MUST reject an awrVersion major it does not implement, so
        # resolving awr 3.x would install an implementation this plugin must refuse.
        assert awr_pins == ["awr>=2.0,<3"]

    def test_the_import_failure_is_an_error_not_a_fallback(self):
        """No degraded mode: a receipt signed by a second canonicalizer verifies for

        nobody but its author (§4.3, Appendix D).
        """
        assert issubclass(ProvenanceDependencyError, ImportError)
        source = (Path(binding.__file__)).read_text()
        assert "raise ProvenanceDependencyError" in source
        # The plugin must not carry its own AWR/2 canonicalization or proof code.
        for name in ("aimarket_provenance/receipt.py", "aimarket_provenance/verifier.py"):
            text = (PYPROJECT.parent / name).read_text()
            assert "utf16" not in text
            assert "eddsa" not in text.replace("eddsa-jcs-2022", "")

    def test_the_real_module_passes_the_gate(self):
        import awr

        assert binding.require_awr2(awr) is awr

    def test_a_wrong_major_version_is_refused(self):
        """§6.4/§3.1: an awrVersion major a verifier does not implement is rejected."""
        import types

        fake = types.ModuleType("awr")
        fake.AWR_VERSION = "3.0.0"
        for name in binding.REQUIRED_NAMES:
            if not hasattr(fake, name):
                setattr(fake, name, object())
        with pytest.raises(ProvenanceDependencyError, match="implements AWR 3.0.0"):
            binding.require_awr2(fake)

    def test_an_incomplete_module_is_refused(self):
        """The repo root holds a package-less `awr/` directory; it must not pass as one."""
        import types

        fake = types.ModuleType("awr")
        fake.AWR_VERSION = "2.0.0"
        with pytest.raises(ProvenanceDependencyError, match="not the AWR/2 reference"):
            binding.require_awr2(fake)

    def test_a_namespace_package_is_refused_by_name(self):
        import types

        fake = types.ModuleType("awr")
        with pytest.raises(ProvenanceDependencyError, match="namespace package"):
            binding.require_awr2(fake)


class TestOneKeyOneIdentity:
    def test_the_did_names_the_hub_signing_key(self, signer):
        """§5.1: the DID *is* the key, so hub and receipts cannot drift apart."""
        key = signing_key_from_signer(signer)
        assert key.public_key_bytes == base64.b64decode(signer.public_key_b64)
        assert did_key_for_signer(signer) == key.did
        assert key.did.startswith("did:key:z6Mk")

    def test_a_key_file_that_disagrees_with_the_signer_raises(self, signer, tmp_path):
        """Silently signing under the wrong did:key is the failure this prevents."""

        class Detached:
            key_path = tmp_path / "detached"
            public_key_b64 = signer.public_key_b64

        Detached.key_path.write_bytes(b"\x01" * 64)
        with pytest.raises(ProvenanceKeyError, match="does not hold the key"):
            signing_key_from_signer(Detached())

    def test_a_missing_key_file_raises(self, tmp_path, signer):
        class Missing:
            key_path = tmp_path / "absent"
            public_key_b64 = signer.public_key_b64

        with pytest.raises(ProvenanceKeyError, match="cannot read"):
            signing_key_from_signer(Missing())

    def test_a_truncated_key_file_raises(self, tmp_path, signer):
        class Short:
            key_path = tmp_path / "short"
            public_key_b64 = signer.public_key_b64

        Short.key_path.write_bytes(b"\x00" * 32)
        with pytest.raises(ProvenanceKeyError, match="64 bytes"):
            signing_key_from_signer(Short())

    def test_a_non_signer_raises(self):
        with pytest.raises(ProvenanceKeyError, match="expected an aimarket_hub"):
            signing_key_from_signer(object())
