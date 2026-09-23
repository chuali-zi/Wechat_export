import sqlite3

import pytest

from wxtext.cipher import open_readonly


def test_readonly_uri_with_special_characters(tmp_path):
    path = tmp_path / "space # percent %.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample(value INTEGER)")
        connection.execute("INSERT INTO sample VALUES(42)")
    before = path.read_bytes()
    connection = open_readonly(path)
    try:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == 42
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO sample VALUES(43)")
    finally:
        connection.close()
    assert path.read_bytes() == before
