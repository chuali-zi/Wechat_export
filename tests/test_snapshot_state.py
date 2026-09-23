import os
import sys

import pytest

from wxtext.errors import ToolError
from wxtext.snapshot import snapshot
from wxtext.state import dpapi


def test_snapshot_detects_same_size_changed_file_even_restored_mtime(account, tmp_path):
    root, _, _ = account
    work = tmp_path / "work"
    work.mkdir()
    calls = 0

    def ensure_stopped():
        nonlocal calls
        calls += 1
        if calls == 3:
            path = root / "contact/contact.db"
            stat = path.stat()
            data = bytearray(path.read_bytes())
            data[50] ^= 1
            path.write_bytes(data)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    with pytest.raises(ToolError, match="SOURCE_CHANGED"):
        with snapshot(root, work, ensure_stopped, settle_seconds=0):
            pytest.fail("should not produce a changed snapshot")
    assert not list(work.iterdir())


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI needs Windows")
def test_real_dpapi_roundtrip():
    original = b"synthetic fixture, not a real WeChat key"
    protected = dpapi(original)
    assert original not in protected
    assert dpapi(protected, decrypt=True) == original
