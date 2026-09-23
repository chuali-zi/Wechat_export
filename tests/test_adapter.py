import pytest
import zstandard

from wxtext.adapter import Contact, decode_text, resolve_target, resolve_self
from wxtext.errors import ToolError
from wxtext.exporter import MessageStore


def test_matching_priorities_and_literal_input():
    contacts = [Contact("id1", "alias", "张三", "朋友"), Contact("id2", "", "id1", "朋友"),
                Contact("1@chatroom", "", "群", "")]
    assert resolve_target(contacts, "id1").user_id == "id1"
    assert resolve_target(contacts, "alias").user_id == "id1"
    with pytest.raises(ToolError, match="AMBIGUOUS_TARGET"):
        resolve_target(contacts, "朋友")
    with pytest.raises(ToolError, match="TARGET_NOT_FOUND"):
        resolve_target(contacts, "' OR 1=1 --")
    with pytest.raises(ToolError, match="UNSUPPORTED_TARGET"):
        resolve_target(contacts, "群")
    with pytest.raises(ToolError, match="SELF_ID_REQUIRED"):
        resolve_self([Contact("me"), Contact("me_abcd")], "me_abcd", None)


def test_text_decoding_is_lossless_and_strict():
    body = " \tHello🙂\r\n中文\n "
    assert decode_text(body) == body
    assert decode_text(zstandard.ZstdCompressor().compress(body.encode())) == body
    assert decode_text(None, None, body) == body
    for invalid, flag in [(b"\xff", 0), (b"not zstd", 4), (b"text", 17)]:
        with pytest.raises(ToolError):
            decode_text(invalid, flag)
    compressed = zstandard.ZstdCompressor().compress(body.encode())
    with pytest.raises(ToolError):
        decode_text(compressed + b"extra bytes", 4)


def test_conflicting_duplicates_are_errors(tmp_path):
    store = MessageStore(tmp_path / "store.sqlite")
    message = {"message_id": "999", "timestamp": 123, "sort_seq": 1, "sender_id": "me", "text": "a",
               "type_code": 1, "sources": [{"file": "message/message_0.db", "table": "Msg_x", "local_id": 1}]}
    try:
        store.add(message)
        with pytest.raises(ToolError, match="MESSAGE_CONFLICT"):
            store.add({**message, "text": "b"})
    finally:
        store.close()
