import pytest
from pydantic import ValidationError

from agent.runtime.incarnation_supervisor import (
    INCARNATION_MANIFEST_FILENAME,
    IncarnationManifest,
    read_manifest,
    write_manifest,
)


def test_manifest_roundtrip(tmp_path):
    m = IncarnationManifest(
        run_id="r1", current_incarnation_idx=2,
        carry_weights_hash="0xabc", max_incarnations=10,
    )
    write_manifest(tmp_path, m)
    assert (tmp_path / INCARNATION_MANIFEST_FILENAME).exists()
    assert read_manifest(tmp_path) == m


def test_read_missing_manifest_returns_none(tmp_path):
    assert read_manifest(tmp_path) is None


def test_read_corrupt_manifest_returns_none(tmp_path):
    (tmp_path / INCARNATION_MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    assert read_manifest(tmp_path) is None  # absent/corrupt → cold start at incarnation 0


def test_extra_forbid(tmp_path):
    with pytest.raises(ValidationError):
        IncarnationManifest(
            run_id="r", current_incarnation_idx=0,
            carry_weights_hash="h", max_incarnations=10, bogus=1,
        )
