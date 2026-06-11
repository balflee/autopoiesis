"""Track B submission package — Day-20 SUBMISSION.json + .md builder.

The Genesis Experiment Day-20 deliverable is a self-contained
submission package the judges ingest:

* ``SUBMISSION.json`` — machine-readable manifest carrying commit
  hash, three-chain deploy table, canonical ABI hashes, IPFS roots,
  demo-video URL+sha, Phase-3 launch tx + role-renunciation event
  txes, and the §15 Gap-7 staging-rehearsal verdict.
* ``SUBMISSION.md`` — human-readable view rendered from the JSON, so
  a judge can read the same evidence narratively.

Modules
-------

``abi_hasher``
    Canonical-form sha256 hasher for the ABI files in
    ``.dev/contracts/*_abi.v*.json``. Sort_keys + no whitespace ⇒
    bytes-stable across formatters + machines.

``build_manifest``
    CLI + library that assembles the manifest. Placeholder-friendly:
    Day-20 may ship sections with ``placeholder=True`` flags while
    real deploys / pins / videos finalise. Invoked as
    ``python -m agent.submission.build_manifest``.

``render_markdown``
    Pure ``SubmissionManifest → str`` renderer with no filesystem
    side-effects.

This package intentionally does NOT eagerly re-export submodule
names. Eager re-exports collide with the ``python -m
agent.submission.build_manifest`` CLI invocation (the package
``__init__`` would load the submodule before ``runpy`` tries to
re-import it as ``__main__``) AND shadow the submodule attribute on
the package namespace (the re-exported ``build_manifest`` function
masks the submodule of the same name). Import directly from the
submodules you need:

.. code-block:: python

    from agent.submission.abi_hasher import hash_abi_payload
    from agent.submission.build_manifest import build_manifest
    from agent.submission.render_markdown import render
"""

from __future__ import annotations

__all__: list[str] = []
