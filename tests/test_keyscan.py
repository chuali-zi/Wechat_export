import struct
import time

import pytest

from wxtext.keyscan import CHUNK_SIZE, LAYOUT, MASTER_PREFIX, KeyMatcher, scan_config, scan_master


class Memory:
    def __init__(self, size):
        self.base = 0x10000
        self.data = bytearray(size)

    def regions(self):
        yield self.base, len(self.data)

    def read(self, address, size):
        offset = address - self.base
        if offset < 0 or offset + size > len(self.data):
            return None
        return bytes(self.data[offset:offset + size])


def test_config_scan_across_chunk_boundary_uses_hmac(tmp_path, native):
    path = tmp_path / "fixture.db"
    key = native.create(path, "CREATE TABLE sample(x);", b"z" * 32)
    memory = Memory(CHUNK_SIZE + 512)
    anchor = CHUNK_SIZE - 10
    memory.data[anchor:anchor + len(LAYOUT.marker)] = LAYOUT.marker
    reference, config, buffer = 64, 512, 2048
    struct.pack_into("<QQ", memory.data, reference, memory.base + anchor, len(LAYOUT.marker))
    struct.pack_into("<Q", memory.data, reference + LAYOUT.reference_to_config, memory.base + config)
    literal = ("x'" + key.secret.hex() + key.salt.hex() + "'").encode()
    encrypted = bytes(value ^ LAYOUT.mask[i % len(LAYOUT.mask)] for i, value in enumerate(literal))
    struct.pack_into("<QQ", memory.data, config + LAYOUT.config_to_buffer, memory.base + buffer, len(encrypted))
    memory.data[buffer:buffer + len(encrypted)] = encrypted
    # Wrong but plausible literal is not accepted.
    wrong = b"x'" + b"a" * 96 + b"'"
    memory.data[3000:3000 + len(wrong)] = wrong
    matcher = KeyMatcher({"a.db": path.read_bytes()[:4096]})
    report = scan_config(memory, matcher, time.monotonic() + 10)
    assert matcher.keys == {"a.db": key}
    assert report["config_references"] == 1


def test_scan_timeout_is_bounded():
    memory = Memory(64)
    matcher = KeyMatcher({"a.db": bytes(4096)})
    result = scan_config(memory, matcher, time.monotonic() - 1)
    assert result["timed_out"] and not matcher.keys


@pytest.mark.parametrize("corrupt", [False, True])
def test_master_config_fallback_derives_and_authenticates(tmp_path, native, corrupt):
    material, mask = bytes(range(32)), bytes(reversed(range(32)))
    database = tmp_path / "sample.db"
    key = native.create(database, "CREATE TABLE a(x);", material, "master")
    memory = Memory(16384)
    marker, parent, config, buffer = 1024, 4096, 8192, 12000
    memory.data[marker:marker + 13] = b"global_config"
    struct.pack_into("<QQ", memory.data, marker + 16, 13, 15)
    struct.pack_into("<Q", memory.data, marker + 16 - 0x138, memory.base + parent)
    struct.pack_into("<Q", memory.data, parent + 0x68, memory.base + config)
    struct.pack_into("<Q", memory.data, config + 0x2B8, memory.base + buffer)
    struct.pack_into("<QQ", memory.data, config + 0x2B8 + 16, 32, 32)
    memory.data[buffer:buffer + 32] = bytes(a ^ b for a, b in zip(material, mask))
    if corrupt:
        memory.data[buffer] ^= 1
    image = bytearray(b"Synthetic landmark: " + MASTER_PREFIX)
    for index in range(4):
        image.extend(mask[index * 8:index * 8 + 8])
        if index < 3:
            image.extend(bytes((0x48, 0x89, 0x44, 0x24, 0x20 + index * 8, 0x48, 0xB8)))
    dll = tmp_path / "synthetic-module.bin"
    dll.write_bytes(image)
    matcher = KeyMatcher({"sample.db": database.read_bytes()[:4096]})
    scan_master(memory, matcher, (memory.base, len(memory.data), str(dll)), time.monotonic() + 5)
    assert matcher.keys == ({} if corrupt else {"sample.db": key})
