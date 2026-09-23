"""Fixtures encrypted by the independent native SQLCipher engine, not wxtext."""
import ctypes as c
import ctypes.util
from pathlib import Path

import pytest
import zstandard

from wxtext.adapter import message_table
from wxtext.cipher import derive_key


class SQLCipher:
    def __init__(self, filename):
        self.lib = c.CDLL(filename)
        self.lib.sqlite3_open.argtypes = [c.c_char_p, c.POINTER(c.c_void_p)]
        self.lib.sqlite3_exec.argtypes = [c.c_void_p, c.c_char_p, c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p)]
        self.lib.sqlite3_close.argtypes = [c.c_void_p]
        self.lib.sqlite3_free.argtypes = [c.c_void_p]
        self.lib.sqlite3_key.argtypes = [c.c_void_p, c.c_void_p, c.c_int]

    def create(self, path: Path, statements: str, material: bytes, kind="raw"):
        handle = c.c_void_p()
        assert self.lib.sqlite3_open(str(path).encode("utf-8"), c.byref(handle)) == 0
        try:
            if kind == "raw":
                self.execute(handle, f'''PRAGMA key = "x'{material.hex()}'";''')
            else:
                buffer = c.create_string_buffer(material)
                assert self.lib.sqlite3_key(handle, buffer, len(material)) == 0
            self.execute(handle, "PRAGMA cipher_compatibility=4; PRAGMA cipher_page_size=4096;")
            self.execute(handle, statements)
        finally:
            assert self.lib.sqlite3_close(handle) == 0
        salt = path.read_bytes()[:16]
        return derive_key(material, salt, kind)

    def execute(self, handle, sql):
        error = c.c_void_p()
        status = self.lib.sqlite3_exec(handle, sql.encode("utf-8"), None, None, c.byref(error))
        if status:
            message = c.string_at(error).decode() if error.value else "native SQLite failure"
            if error.value:
                self.lib.sqlite3_free(error)
            pytest.fail(message)


@pytest.fixture(scope="session")
def native():
    import os
    filename = os.environ.get("WXTEXT_TEST_SQLCIPHER") or ctypes.util.find_library("sqlcipher")
    if not filename:
        pytest.skip("Independent SQLCipher library unavailable; set WXTEXT_TEST_SQLCIPHER to its path")
    return SQLCipher(filename)


def sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return "x'" + value.hex() + "'"
    if isinstance(value, int):
        return str(value)
    return "'" + value.replace("'", "''") + "'"


@pytest.fixture
def account(tmp_path, native):
    root = tmp_path / "微信 数据" / "wxid_me_abcd" / "db_storage"
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir()
    contacts = """CREATE TABLE contact(username TEXT PRIMARY KEY, alias TEXT, nick_name TEXT, remark TEXT);
        INSERT INTO contact VALUES('wxid_me','my_account','自己','');
        INSERT INTO contact VALUES('wxid_peer','peer_alias','张三','老朋友');
        INSERT INTO contact VALUES('wxid_other','other_alias','张三','同事');
        INSERT INTO contact VALUES('123@chatroom','','群聊','');
    """
    keys = {}
    keys["contact/contact.db"] = native.create(root / "contact/contact.db", contacts, bytes(range(32)))
    body = "  多行🙂\n" + "长文字测试 " * 10000 + "\n\t 保留空格  "
    messages = [
        [(1, 100, 1, 20, 7, 1700000001, "我发出的消息", 0),
         (2, 101, 1, 21, 13, 1700000001, zstandard.ZstdCompressor().compress(body.encode()), 4),
         (3, 0, 1, 22, 13, 1700000002, "相同文字", 0),
         (4, 0, 3, 23, 13, 1700000003, b"\xff\xff", 0)],
        [(9, 100, 1, 20, 42, 1700000001, "我发出的消息", 0),
         (10, 0, 1, 24, 1, 1700000004, "相同文字", 0),
         (11, 102, 1, 25, 1, 1700000005, "最近一条\n完整结尾", 0)],
    ]
    table = message_table("wxid_peer")
    for index, rows in enumerate(messages):
        self_row, peer_row = (7, 13) if index == 0 else (42, 1)
        schema = f"""CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY, is_session INTEGER);
            INSERT INTO Name2Id(rowid,user_name) VALUES({self_row},'wxid_me'),({peer_row},'wxid_peer');
            CREATE TABLE {table}(local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER,
                sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content TEXT,
                WCDB_CT_message_content INTEGER);
            CREATE TABLE {message_table('wxid_other')}(local_id INTEGER PRIMARY KEY, secret TEXT);
            INSERT INTO {message_table('wxid_other')} VALUES(1,'不属于目标的秘密内容');
        """
        for row in rows:
            schema += f"INSERT INTO {table} VALUES({','.join(sql_value(v) for v in row)});\n"
        relative = f"message/message_{index}.db"
        keys[relative] = native.create(root / relative, schema, bytes([index + 50]) * 32)
    return root, keys, body
