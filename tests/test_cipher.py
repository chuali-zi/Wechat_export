import sqlite3

import pytest

from wxtext.cipher import DatabaseKey, PageCipher, decrypt_database, verify_key
from wxtext.errors import ToolError


@pytest.mark.parametrize("kind", ["raw", "master"])
def test_independent_sqlcipher_roundtrip(tmp_path, native, kind):
    encrypted, plain = tmp_path / "encrypted.db", tmp_path / "plain.db"
    key = native.create(encrypted, "CREATE TABLE sample(id INTEGER PRIMARY KEY, content TEXT);"
                        "INSERT INTO sample(content) VALUES('Unicode 中文🙂'),(hex(randomblob(25000)));",
                        bytes(range(32)), kind)
    assert encrypted.stat().st_size > 4096
    before = encrypted.read_bytes()
    assert verify_key(key, before[:4096])
    decrypt_database(encrypted, plain, key)
    connection = sqlite3.connect(plain)
    assert connection.execute("SELECT content FROM sample WHERE id=1").fetchone() == ("Unicode 中文🙂",)
    assert connection.execute("SELECT length(content) FROM sample WHERE id=2").fetchone() == (50000,)
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    connection.close()
    assert encrypted.read_bytes() == before
    assert key.secret.hex() not in repr(key)


def test_authentication_checks_every_page_and_cleans_partial_output(tmp_path, native):
    source, output = tmp_path / "cipher.db", tmp_path / "plain.db"
    key = native.create(source, "CREATE TABLE a(x); INSERT INTO a VALUES(randomblob(9000));", b"k" * 32)
    data = bytearray(source.read_bytes())
    data[4096 + 37] ^= 1
    source.write_bytes(data)
    with pytest.raises(ToolError, match="PAGE_AUTH_FAILED") as error:
        decrypt_database(source, output, key)
    assert error.value.details["page"] == 2
    assert not output.exists()


def test_wrong_key_salt_page_number_and_truncation(tmp_path, native):
    source = tmp_path / "cipher.db"
    key = native.create(source, "CREATE TABLE a(x);", b"a" * 32)
    page = source.read_bytes()[:4096]
    assert not verify_key(DatabaseKey(b"b" * 32, key.salt), page)
    assert not verify_key(DatabaseKey(key.secret, b"c" * 16), page)
    assert not PageCipher(key).authenticate(page, 2)
    source.write_bytes(source.read_bytes()[:-1])
    with pytest.raises(ToolError, match="UNSUPPORTED_FORMAT"):
        decrypt_database(source, tmp_path / "out.db", key)


def test_existing_output_is_never_deleted(tmp_path, native):
    source, output = tmp_path / "cipher.db", tmp_path / "out.db"
    key = native.create(source, "CREATE TABLE a(x);", b"x" * 32)
    output.write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        decrypt_database(source, output, key)
    assert output.read_bytes() == b"keep me"
