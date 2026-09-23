import hashlib
import os

import pytest

from wxtext.errors import ToolError
from wxtext.snapshot import CONTACT, fingerprint, snapshot


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "account"
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir()
    (root / CONTACT).write_bytes(b"synthetic encrypted contact bytes")
    (root / "message/message_0.db").write_bytes(b"synthetic encrypted message bytes")
    work = tmp_path / "work"
    work.mkdir()
    return root, work


@pytest.mark.parametrize("wal_bytes", [b"", b"synthetic encrypted WAL bytes"])
def test_snapshot_copies_wal_with_nested_fingerprint_without_changing_source(source, wal_bytes):
    root, work = source
    wal_name = "message/message_0.db-wal"
    (root / wal_name).write_bytes(wal_bytes)
    before = {p.relative_to(root).as_posix(): fingerprint(p)
              for p in root.rglob("*") if p.is_file()}
    calls = []
    with snapshot(root, work, lambda: calls.append(True), settle_seconds=0) as (copy, manifest):
        assert len(calls) == 3
        assert manifest == [
            {"file": name, "size": before[name][0], "sha256": before[name][2],
             **({"wal": {"file": wal_name, "size": len(wal_bytes),
                         "sha256": hashlib.sha256(wal_bytes).hexdigest()}}
                if name == "message/message_0.db" else {})}
            for name in [CONTACT, "message/message_0.db"]
        ]
        assert {p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.is_file()} == set(before)
        for name in before:
            assert (copy / name).read_bytes() == (root / name).read_bytes()
        (copy / wal_name).write_bytes(b"private copy only")
        (copy / "message/message_0.db").write_bytes(b"private replay only")
    assert before == {name: fingerprint(root / name) for name in before}
    assert not copy.exists()
    assert not list(work.iterdir())


@pytest.mark.parametrize("change", ["appears", "disappears", "mutates"])
def test_snapshot_detects_wal_change_before_final_validation(source, change):
    root, work = source
    wal = root / (CONTACT + "-wal")
    if change != "appears":
        wal.write_bytes(b"original")
    calls = 0

    def ensure_stopped():
        nonlocal calls
        calls += 1
        if calls == 3:
            if change == "disappears":
                wal.unlink()
            elif change == "mutates":
                stat = wal.stat()
                wal.write_bytes(b"modified")
                os.utime(wal, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            else:
                wal.write_bytes(b"new WAL")

    with pytest.raises(ToolError, match="SOURCE_CHANGED"):
        with snapshot(root, work, ensure_stopped, settle_seconds=0):
            pytest.fail("An unstable snapshot must not be yielded")
    assert not list(work.iterdir())


def test_snapshot_detects_wal_mutation_before_copy(source):
    root, work = source
    wal = root / (CONTACT + "-wal")
    wal.write_bytes(b"original")
    calls = 0

    def ensure_stopped():
        nonlocal calls
        calls += 1
        if calls == 2:
            wal.write_bytes(b"modified")

    with pytest.raises(ToolError, match="SOURCE_CHANGED"):
        with snapshot(root, work, ensure_stopped, settle_seconds=0):
            pytest.fail("A changed WAL must not be copied successfully")
    assert not list(work.iterdir())


@pytest.mark.parametrize("kind", ["directory", "outside_symlink"])
def test_snapshot_rejects_unsafe_wal_sidecar(source, tmp_path, kind):
    root, work = source
    wal = root / (CONTACT + "-wal")
    if kind == "directory":
        wal.mkdir()
    else:
        outside = tmp_path / "outside-wal"
        outside.write_bytes(b"outside account")
        try:
            wal.symlink_to(outside)
        except OSError as error:
            pytest.skip(f"Symlinks unavailable: {error}")
    with pytest.raises(ToolError, match="UNSAFE_PATH"):
        with snapshot(root, work, lambda: None, settle_seconds=0):
            pytest.fail("An unsafe WAL must not be captured")
    assert not list(work.iterdir())
