"""SQLite framing oracle plus synthetic HMACs, not native SQLCipher validation."""
import hashlib
import hmac
import sqlite3
import struct

import pytest

from wxtext.cipher import DatabaseKey
from wxtext.errors import ToolError
from wxtext.wal import apply_wal


KEY = DatabaseKey(bytes(range(32)), bytes(range(16)))
PAGE = 4096
FRAME = PAGE + 24


def checksum(data, order, seed=(0, 0)):
    words = struct.unpack(order + str(len(data) // 4) + "I", data)
    a, b = seed
    for i in range(0, len(words), 2):
        a = (a + words[i] + b) % 2**32
        b = (b + words[i + 1] + a) % 2**32
    return a, b


def build_wal(frames, order="<"):
    header = struct.pack(">6I", 0x377F0682 if order == "<" else 0x377F0683,
                         3007000, PAGE, 0, 123, 456)
    seed = checksum(header, order)
    output = header + struct.pack(">2I", *seed)
    for number, size, page in frames:
        prefix = struct.pack(">4I", number, size, 123, 456)
        seed = checksum(prefix[:8] + page, order, seed)
        output += prefix + struct.pack(">2I", *seed) + page
    return output


@pytest.fixture
def plaintext(monkeypatch):
    monkeypatch.setattr("wxtext.wal.PageCipher.authenticate", lambda self, page, number: True)


def install(tmp_path, wal, pages=1):
    path = tmp_path / "private.db"
    path.write_bytes(b"O" * PAGE * pages)
    sidecar = tmp_path / "private.db-wal"
    sidecar.write_bytes(wal)
    return path, sidecar


def rejected(path, sidecar, code="INVALID_WAL"):
    before = path.read_bytes(), sidecar.read_bytes()
    with pytest.raises(ToolError, match=code):
        apply_wal(path, KEY)
    assert (path.read_bytes(), sidecar.read_bytes()) == before


def test_absent_empty(tmp_path):
    path = tmp_path / "nonexistent.db"
    assert apply_wal(path, KEY)["status"] == "absent"
    (tmp_path / "nonexistent.db-wal").touch()
    assert apply_wal(path, KEY)["status"] == "empty"
    assert not path.exists()


@pytest.mark.parametrize("order", ["<", ">"])
def test_sqlite_generated_commits(tmp_path, plaintext, order):
    source = tmp_path / "oracle.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA page_size=4096")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE sample(value)")
        connection.commit()
        connection.execute("INSERT INTO sample VALUES(?)", ("a" * 12000,))
        connection.commit()
        connection.execute("UPDATE sample SET value='latest'")
        connection.commit()
        raw = PathWal(source).read_bytes()
        native_order = "<" if struct.unpack(">I", raw[:4])[0] == 0x377F0682 else ">"
        frames = []
        for offset in range(32, len(raw), FRAME):
            number, size = struct.unpack(">2I", raw[offset:offset + 8])
            frames.append((number, size, raw[offset + 24:offset + FRAME]))
        # Native order uses SQLite's bytes unchanged; the opposite order only
        # changes checksum encoding, keeping SQLite-generated frame contents.
        wal = raw if order == native_order else build_wal(frames, order)
        path, sidecar = install(tmp_path, wal)
        path.write_bytes(source.read_bytes())
        original = source.read_bytes(), PathWal(source).read_bytes()
        metadata = apply_wal(path, KEY)
        assert metadata["commits"] == 3
        assert metadata["committed_frames"] == len(frames)
        assert metadata["ignored_frames"] == 0
        assert sidecar.read_bytes() == wal
        assert (source.read_bytes(), PathWal(source).read_bytes()) == original
        with sqlite3.connect(path.as_uri() + "?immutable=1", uri=True) as replay:
            assert replay.execute("SELECT value FROM sample").fetchall() == [("latest",)]
            assert replay.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()


def PathWal(path):
    return path.with_name(path.name + "-wal")


@pytest.mark.parametrize("order", ["<", ">"])
def test_latest_commit_and_validated_tail(tmp_path, plaintext, order):
    frames = [(1, 1, b"A" * PAGE), (1, 0, b"B" * PAGE),
              (2, 2, b"C" * PAGE), (1, 0, b"D" * PAGE)]
    path, sidecar = install(tmp_path, build_wal(frames, order))
    assert apply_wal(path, KEY) == {
        "status": "replayed", "frames": 4, "commits": 2, "committed_frames": 3,
        "ignored_frames": 1, "stale_frames": 0, "stale_bytes": 0,
        "pages_replayed": 2, "database_pages": 2}
    assert path.read_bytes() == b"B" * PAGE + b"C" * PAGE


@pytest.mark.parametrize("frames", [[], [(1, 0, b"A" * PAGE)]])
def test_no_commit(tmp_path, plaintext, frames):
    path, sidecar = install(tmp_path, build_wal(frames))
    before = path.read_bytes()
    result = apply_wal(path, KEY)
    assert result["status"] == "uncommitted"
    assert result["ignored_frames"] == len(frames)
    assert path.read_bytes() == before


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("damage", ["header", "frame", "tail", "extra", "short",
                                    "magic", "version", "page_size"])
def test_corruption_never_mutates(tmp_path, plaintext, order, damage):
    wal = bytearray(build_wal([(1, 1, b"A" * PAGE), (1, 0, b"B" * PAGE)], order))
    offsets = {"header": 24, "frame": 32 + 16, "tail": 32 + FRAME + 100,
               "magic": 0, "version": 4, "page_size": 8}
    if damage == "extra":
        wal += b"x"
    elif damage == "short":
        wal = wal[:20]
    else:
        wal[offsets[damage]] ^= 1
    path, sidecar = install(tmp_path, wal)
    rejected(path, sidecar)


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("prefix", [0, 1, 2])
def test_recycled_tail_stops_at_first_salt_mismatch(tmp_path, monkeypatch, order, prefix):
    frames = [(1, 1, b"A" * PAGE), (1, 0, b"B" * PAGE)][:prefix]
    wal = bytearray(build_wal(frames + [(1, 1, b"S" * PAGE), (1, 1, b"T" * PAGE)], order))
    offset = 32 + prefix * FRAME
    # Stale numbers, commit sizes, checksums and page authentication must not
    # be examined. Even a later matching salt cannot restart the generation.
    wal[offset:offset + 24] = struct.pack(">6I", 0, 0xFFFFFFFF, 999, 456, 0, 0)
    calls = []

    def authenticate(self, page, number):
        calls.append((page, number))
        return page in (b"A" * PAGE, b"B" * PAGE)

    monkeypatch.setattr("wxtext.wal.PageCipher.authenticate", authenticate)
    path, sidecar = install(tmp_path, wal)
    assert apply_wal(path, KEY) == {
        "status": "replayed" if prefix else "uncommitted", "frames": prefix,
        "commits": int(prefix > 0), "committed_frames": int(prefix > 0),
        "ignored_frames": int(prefix == 2), "stale_frames": 2, "stale_bytes": 2 * FRAME,
        "pages_replayed": int(prefix > 0), "database_pages": 1}
    assert calls == [(page, number) for number, _, page in frames]
    assert path.read_bytes() == (b"A" if prefix else b"O") * PAGE
    assert sidecar.read_bytes() == wal


@pytest.mark.parametrize("order", ["<", ">"])
def test_stale_tail_does_not_hide_active_corruption(tmp_path, plaintext, order):
    wal = bytearray(build_wal([(1, 1, b"A" * PAGE), (1, 0, b"B" * PAGE),
                               (1, 1, b"S" * PAGE)], order))
    wal[32 + 2 * FRAME + 8] ^= 1
    wal[32 + FRAME + 16] ^= 1
    wal += b"partial stale frame"
    path, sidecar = install(tmp_path, wal)
    rejected(path, sidecar)


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("suffix_size", [24, 112, FRAME + 112, 989 * FRAME + 112])
def test_partial_suffix_after_mismatching_header(tmp_path, monkeypatch, order, suffix_size):
    frames = [(1, int(i in (5, 10, 15, 20, 29)), bytes([i]) * PAGE)
              for i in range(1, 30)]
    prefix = build_wal(frames, order)
    suffix = struct.pack(">6I", 0, 0xFFFFFFFF, 999, 456, 0, 0) + b"X" * (suffix_size - 24)
    calls = []

    def authenticate(self, page, number):
        calls.append((page, number))
        return page != b"X" * PAGE

    monkeypatch.setattr("wxtext.wal.PageCipher.authenticate", authenticate)
    path, sidecar = install(tmp_path, prefix + suffix)
    assert apply_wal(path, KEY) == {
        "status": "replayed", "frames": 29, "commits": 5, "committed_frames": 29,
        "ignored_frames": 0, "stale_frames": suffix_size // FRAME,
        "stale_bytes": suffix_size, "pages_replayed": 1, "database_pages": 1}
    assert calls == [(page, number) for number, _, page in frames]
    assert path.read_bytes() == bytes([29]) * PAGE
    assert sidecar.read_bytes() == prefix + suffix
    if suffix_size == 989 * FRAME + 112:
        assert sidecar.stat().st_size == 4194304


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("tail_size", [1, 16, 23, 24, 112, FRAME - 1])
def test_incomplete_active_frame_rejected(tmp_path, plaintext, order, tail_size):
    wal = build_wal([(1, 1, b"A" * PAGE), (1, 0, b"B" * PAGE)], order)
    path, sidecar = install(tmp_path, wal[:32 + FRAME + tail_size])
    rejected(path, sidecar)


def test_short_mismatching_header_is_not_generation_boundary(tmp_path, plaintext):
    wal = build_wal([(1, 1, b"A" * PAGE)])
    wal += struct.pack(">6I", 0, 0, 999, 456, 0, 0)[:23]
    path, sidecar = install(tmp_path, wal)
    rejected(path, sidecar)


@pytest.mark.parametrize("frames", [
    [(0, 1, b"A" * PAGE)], [(100, 1, b"A" * PAGE)],
    [(1, 100, b"A" * PAGE)], [(3, 3, b"A" * PAGE)],
    [(1, 1, b"A" * PAGE), (3, 3, b"B" * PAGE)],
])
def test_bounds_and_growth_gaps(tmp_path, plaintext, frames):
    path, sidecar = install(tmp_path, build_wal(frames), pages=3 if len(frames) == 2 else 1)
    rejected(path, sidecar)


def test_shrink_then_regrow_and_final_truncate(tmp_path, plaintext):
    frames = [(2, 3, b"A" * PAGE), (1, 1, b"B" * PAGE),
              (2, 2, b"C" * PAGE)]
    path, sidecar = install(tmp_path, build_wal(frames), pages=3)
    assert apply_wal(path, KEY)["database_pages"] == 2
    assert path.read_bytes() == b"B" * PAGE + b"C" * PAGE


def authenticated_page(number):
    # Synthetic ciphertext/IV bytes and independently computed SQLCipher-style
    # HMAC. This does not assert native SQLCipher WAL interoperability.
    body = (KEY.salt + b"E" * (PAGE - 80)) if number == 1 else b"E" * (PAGE - 64)
    mac_key = hashlib.pbkdf2_hmac("sha512", KEY.secret,
                                bytes(value ^ 0x3A for value in KEY.salt), 2, 32)
    digest = hmac.digest(mac_key, body[16 if number == 1 else 0:] + struct.pack("<I", number), "sha512")
    return body + digest


@pytest.mark.parametrize("damage", [None, "hmac", "salt", "number", "uncommitted"])
def test_real_page_authentication(tmp_path, damage):
    first, second = authenticated_page(1), authenticated_page(2)
    if damage == "salt":
        first = b"X" + first[1:]
    if damage in ("hmac", "uncommitted"):
        second = second[:-1] + bytes([second[-1] ^ 1])
    if damage == "number":
        second = first
    frames = [(1, 1, first), (2, 0 if damage == "uncommitted" else 2, second)]
    path, sidecar = install(tmp_path, build_wal(frames))
    if damage:
        rejected(path, sidecar, "PAGE_AUTH_FAILED")
    else:
        assert apply_wal(path, KEY)["pages_replayed"] == 2
        assert path.read_bytes() == first + second
