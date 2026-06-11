"""Tests for :mod:`agent.submission.abi_hasher`.

Covers the two reproducibility properties the SUBMISSION manifest
contract anchors on:

1. **Determinism across runs** — same input ⇒ same hash, every time,
   in any process / on any machine.
2. **Whitespace insensitivity** — pretty-printed and minified copies
   of the same ABI hash to the same digest. This is what lets a
   contributor reformat ``.dev/contracts/*_abi.v*.json`` without
   silently invalidating the manifest.

Plus:

* Sort-key canonicalisation — semantically equal dicts with different
  insertion order produce the same hash.
* Detection of REAL changes — adding a field flips the hash.
* Envelope-vs-array semantics — ``hash_abi_payload`` extracts the
  ``abi`` key from a registry envelope but accepts a bare ABI array.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.submission.abi_hasher import (
    canonical_serialize,
    canonical_sha256,
    hash_abi_file,
    hash_abi_payload,
)


# A tiny but realistic Solidity ABI fragment — function + event.
_SAMPLE_ABI: list[dict[str, object]] = [
    {
        "type": "function",
        "name": "lockPhase3",
        "inputs": [],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "event",
        "name": "Phase3RolesRenounced",
        "anonymous": False,
        "inputs": [
            {"name": "lockedAt", "type": "uint64", "indexed": False, "internalType": "uint64"}
        ],
    },
]


# ── canonical_serialize ──────────────────────────────────────────────


def test_canonical_serialize_sorts_keys() -> None:
    """Insertion order of dict keys MUST NOT affect output bytes."""
    a = {"b": 1, "a": 2, "c": [1, 2, 3]}
    b = {"a": 2, "c": [1, 2, 3], "b": 1}
    assert canonical_serialize(a) == canonical_serialize(b)


def test_canonical_serialize_no_whitespace() -> None:
    """Output MUST contain no spaces/newlines (minified separators)."""
    payload = {"name": "x", "args": [1, 2, {"k": "v"}]}
    out = canonical_serialize(payload)
    # No space, no tab, no newline anywhere in the encoded bytes.
    assert b" " not in out
    assert b"\t" not in out
    assert b"\n" not in out


def test_canonical_serialize_returns_bytes() -> None:
    """canonical_serialize returns bytes (so it feeds straight into hashlib)."""
    assert isinstance(canonical_serialize({"k": "v"}), bytes)


# ── canonical_sha256 ─────────────────────────────────────────────────


def test_canonical_sha256_is_64_hex() -> None:
    """sha256 hex is exactly 64 lower-case hex chars."""
    digest = canonical_sha256({"hello": "world"})
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


# ── hash_abi_payload — determinism + whitespace insensitivity ────────


def test_hash_abi_payload_deterministic_across_runs() -> None:
    """Hashing the same ABI repeatedly MUST always return the same digest."""
    digests = {hash_abi_payload(_SAMPLE_ABI) for _ in range(50)}
    assert len(digests) == 1


def test_hash_abi_payload_whitespace_insensitive() -> None:
    """Pretty-printed and minified files of the SAME ABI hash equally.

    This is the core invariant the SUBMISSION manifest contract relies
    on: a contributor running ``jq -S`` on a registry file does NOT
    invalidate the manifest.
    """
    pretty = json.dumps(_SAMPLE_ABI, indent=4)
    mini = json.dumps(_SAMPLE_ABI, separators=(",", ":"))
    pretty_loaded = json.loads(pretty)
    mini_loaded = json.loads(mini)
    assert hash_abi_payload(pretty_loaded) == hash_abi_payload(mini_loaded)


def test_hash_abi_payload_keys_order_insensitive() -> None:
    """Reordering keys within ABI entries MUST NOT change the hash."""
    reversed_keys_abi: list[dict[str, object]] = []
    for entry in _SAMPLE_ABI:
        reversed_keys_abi.append({k: entry[k] for k in reversed(list(entry.keys()))})
    assert hash_abi_payload(_SAMPLE_ABI) == hash_abi_payload(reversed_keys_abi)


def test_hash_abi_payload_envelope_extracts_abi_field() -> None:
    """Envelope dicts (registry-file shape) hash the same as their bare ``abi``.

    ``.dev/contracts/*_abi.v*.json`` wraps the actual ABI in a metadata
    envelope; the hasher reaches inside automatically so envelope
    edits (description, spec_anchors, version notes) do NOT invalidate
    the digest.
    """
    envelope = {
        "version": "1.2.3",
        "description": "anything",
        "spec_anchors": ["ignored"],
        "abi": _SAMPLE_ABI,
    }
    assert hash_abi_payload(envelope) == hash_abi_payload(_SAMPLE_ABI)


def test_hash_abi_payload_envelope_metadata_changes_do_not_affect_hash() -> None:
    """Two envelopes wrapping the SAME ABI but with different metadata hash equally."""
    envelope_a = {
        "version": "0.1.0",
        "description": "draft a",
        "abi": _SAMPLE_ABI,
    }
    envelope_b = {
        "version": "0.2.0",  # version bump, same wire interface
        "description": "draft b",
        "abi": _SAMPLE_ABI,
        "spec_anchors": ["foo"],  # new field, doesn't matter
    }
    assert hash_abi_payload(envelope_a) == hash_abi_payload(envelope_b)


def test_hash_abi_payload_real_change_flips_hash() -> None:
    """Adding ONE field to the ABI itself MUST change the hash.

    Sanity check that the canonical form isn't collapsing real
    differences.
    """
    modified = list(_SAMPLE_ABI)
    modified.append(
        {
            "type": "function",
            "name": "isPhase3Locked",
            "inputs": [],
            "outputs": [{"name": "", "type": "bool"}],
            "stateMutability": "view",
        }
    )
    assert hash_abi_payload(_SAMPLE_ABI) != hash_abi_payload(modified)


def test_hash_abi_payload_accepts_bare_list() -> None:
    """Passing the bare ABI list is the same as passing it inside ``abi`` key."""
    assert hash_abi_payload(_SAMPLE_ABI) == hash_abi_payload({"abi": _SAMPLE_ABI})


def test_hash_abi_payload_rejects_unsupported_type() -> None:
    """Non-list, non-dict input raises ``TypeError`` with the type name."""
    with pytest.raises(TypeError, match="str"):
        hash_abi_payload("not an abi")


# ── hash_abi_file — file-level convenience ───────────────────────────


def test_hash_abi_file_matches_payload(tmp_path: Path) -> None:
    """``hash_abi_file`` agrees with ``hash_abi_payload(json.load(...))``."""
    envelope = {"version": "0.1.0", "abi": _SAMPLE_ABI}
    f = tmp_path / "sample_abi.v0.1.0.json"
    f.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    assert hash_abi_file(f) == hash_abi_payload(envelope)


def test_hash_abi_file_pretty_vs_mini_same_digest(tmp_path: Path) -> None:
    """Two files (pretty + mini) describing the same ABI hash equally on disk."""
    envelope = {"version": "0.1.0", "abi": _SAMPLE_ABI}
    pretty = tmp_path / "pretty.json"
    mini = tmp_path / "mini.json"
    pretty.write_text(json.dumps(envelope, indent=4), encoding="utf-8")
    mini.write_text(json.dumps(envelope, separators=(",", ":")), encoding="utf-8")
    assert hash_abi_file(pretty) == hash_abi_file(mini)


def test_hash_abi_file_raises_on_invalid_json(tmp_path: Path) -> None:
    """Corrupt JSON is a hard failure, not a silent zero-hash."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        hash_abi_file(bad)
