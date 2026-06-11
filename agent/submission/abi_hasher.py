"""Canonical-form sha256 hasher for Solidity ABIs.

Spec
----

The Genesis Experiment submission package (``SUBMISSION.json``) carries
a tamper-evident hash of every deployed contract's ABI so judges can
verify the bytecode the agent talked to MATCHES the bytecode deployed
on each of the three testnets (PRD §10: Robinhood Chain / Arbitrum
Sepolia / Polygon Amoy ship the **same** ``.sol`` with the **same** ABI
via a single ``Deploy.s.sol`` script). Two reproducibility properties
must hold:

1. **Determinism across runs.** Hashing the same ABI on two different
   machines / pythons / file-system encodings produces the SAME hash.
2. **Whitespace insensitivity.** Pretty-printed and minified ABI files
   that describe the same interface hash to the same value. This means
   a contributor reformatting ``.dev/contracts/*_abi.v*.json`` with
   ``jq -S`` or ``python -m json.tool`` does NOT silently invalidate
   the submission manifest.

We canonicalise both properties by JSON-serialising the parsed payload
with ``sort_keys=True``, ``separators=(",", ":")`` (no whitespace at
all), and ``ensure_ascii=False`` (the registry uses no non-ASCII chars
today but we still want byte-stable output across Python builds with
different default encodings).

Why we hash the ``abi`` *array*, not the whole registry file
------------------------------------------------------------

The ABI registry files at ``.dev/contracts/*_abi.v*.json`` are envelopes:

.. code-block:: json

    {
      "version": "0.4.0",
      "tracks": { ... },
      "spec_anchors": [ ... ],
      "abi": [ <the actual ABI> ]
    }

The ``abi`` field is the Solidity-level wire interface. The envelope
fields (``version``, ``description``, ``spec_anchors``, ...) are
documentation metadata that a contributor SHOULD be free to edit
without re-anchoring the submission manifest. So the canonical
:func:`hash_abi_payload` always reaches into ``payload["abi"]`` if the
payload is a dict that contains that key; if it is already a list, we
hash it as-is. Callers that want to hash the whole envelope can call
:func:`canonical_sha256` directly.

Public surface
--------------

* :func:`canonical_serialize` — bytes-stable JSON encoder.
* :func:`canonical_sha256` — sha256 hex of the canonical encoding.
* :func:`hash_abi_payload` — sha256 of the ABI array inside a payload.
* :func:`hash_abi_file` — load a registry file and hash its ABI.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final

# Canonical JSON separators. ``(",", ":")`` is the minimum-whitespace
# form the JSON spec allows. Anchored as a module constant so the same
# tuple flows through every serialise call (avoids subtle drift from a
# typo elsewhere).
_CANONICAL_SEPARATORS: Final[tuple[str, str]] = (",", ":")


def canonical_serialize(payload: Any) -> bytes:
    """Serialise *payload* to canonical UTF-8 JSON bytes.

    Canonical means:

    * ``sort_keys=True`` — every object's keys appear in lexicographic
      order, so ``{"a":1,"b":2}`` and ``{"b":2,"a":1}`` hash equally.
    * ``separators=(",", ":")`` — no whitespace between tokens, so a
      pretty-printed file and a minified file produce identical bytes.
    * ``ensure_ascii=False`` — non-ASCII characters survive as UTF-8
      bytes rather than being escaped to ``\\uXXXX`` (today the
      registry is pure ASCII; this is defensive).

    The function does NOT mutate *payload*. Returns ``bytes`` (not
    ``str``) because every consumer feeds the result directly into a
    hash function and ``hashlib.sha256`` wants ``bytes``.

    Raises
    ------
    TypeError
        If *payload* contains a non-JSON-serialisable value (e.g. a
        ``datetime`` or ``Path``). Caller's responsibility — the
        registry payloads are pure JSON by design.
    """
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=_CANONICAL_SEPARATORS,
        ensure_ascii=False,
    )
    return text.encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    """Return the sha256 hex digest of :func:`canonical_serialize`.

    Lower-case 64-char hex (matches ``hashlib`` default). The 0x
    prefix is intentionally NOT added — callers that want
    Etherscan-style display can prepend it themselves; the manifest
    stores the raw digest so two manifests are bytes-comparable.
    """
    return hashlib.sha256(canonical_serialize(payload)).hexdigest()


def hash_abi_payload(payload: Any) -> str:
    """Hash the canonical form of an ABI.

    *payload* may be either:

    * A ``list`` — taken to be the ABI array directly.
    * A ``dict`` containing an ``"abi"`` key — its ``abi`` value is
      hashed (envelope metadata is ignored, see module docstring).
    * A ``dict`` WITHOUT an ``"abi"`` key — hashed as-is (escape hatch
      for ABIs that have not been wrapped in an envelope yet).

    Returns the same lower-case 64-char hex as :func:`canonical_sha256`.

    Raises
    ------
    TypeError
        If *payload* is not a list, dict, or any other JSON-encodable
        type. The error message names the actual type so a regression
        (e.g. someone passing a Path) is immediately obvious.
    """
    if isinstance(payload, dict) and "abi" in payload:
        return canonical_sha256(payload["abi"])
    if isinstance(payload, (list, dict)):
        return canonical_sha256(payload)
    raise TypeError(
        f"hash_abi_payload expects list or dict, got {type(payload).__name__}"
    )


def hash_abi_file(path: Path) -> str:
    """Load a JSON file from *path* and hash its ABI per :func:`hash_abi_payload`.

    The file MUST be valid JSON. The function does NOT swallow JSON
    decode errors — a corrupted registry file is a hard build failure,
    not a silent zero-hash.
    """
    raw = path.read_text(encoding="utf-8")
    payload: Any = json.loads(raw)
    return hash_abi_payload(payload)


__all__ = [
    "canonical_serialize",
    "canonical_sha256",
    "hash_abi_file",
    "hash_abi_payload",
]
