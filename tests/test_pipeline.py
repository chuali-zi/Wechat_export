import hashlib
import json
from pathlib import Path
import sys

import pytest
from cryptography.fernet import Fernet

from wxtext.cli import main
from wxtext.adapter import message_table
from wxtext.errors import ToolError
from wxtext.service import ExportService
from wxtext.state import StateStore


class FakeWindows:
    def __init__(self, keys):
        self.keys = keys
        self.running = True
        self.acquisitions = 0

    def processes(self):
        return []

    def ensure_stopped(self):
        if self.running:
            raise ToolError("NEED_EXIT", "test client running")

    def acquire(self, headers, existing, timeout, progress):
        self.acquisitions += 1
        return self.keys, {"fixture": True}


@pytest.fixture
def pipeline(account, tmp_path):
    root, keys, body = account
    # Test-only injected protection. The actual CLI only exposes Windows DPAPI.
    protector = Fernet(Fernet.generate_key())
    state = StateStore(tmp_path / "state", protect=protector.encrypt, unprotect=protector.decrypt)
    windows = FakeWindows(keys)
    service = ExportService(state, windows)
    service.prepare(root)
    windows.running = False
    return service, windows, root, tmp_path / "exports", body


def test_end_to_end_target_only_all_shards_repeatable_and_clean(pipeline):
    service, windows, root, output, body = pipeline
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.db")}
    result = service.export("老朋友", output)
    assert result["message_count"] == 5
    assert result["duplicates_removed"] == 1
    destination = Path(result["output"])
    data = [json.loads(line) for line in (destination / "messages.jsonl").read_text().splitlines()]
    assert data[0]["sender_id"] == "wxid_me"
    assert data[1]["text"] == body
    assert data[1]["sender_id"] == "wxid_peer"
    assert len(data[0]["sources"]) == 2
    assert sum(message["text"] == "相同文字" for message in data) == 2
    assert data[-1]["text"] == "最近一条\n完整结尾"
    assert "不属于目标的秘密内容" not in (destination / "messages.txt").read_text()
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["statistics"]["skipped_type_3"] == 1
    assert manifest["statistics"]["shards_with_target"] == 2
    repeated = service.export("peer_alias", output)
    assert repeated["output"] == result["output"] and repeated["reused"]
    assert not list(service.state.work_root().iterdir())
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.db")}
    assert windows.acquisitions == 1


def test_ambiguous_target_and_live_process_fail_without_output(pipeline):
    service, windows, _, output, _ = pipeline
    with pytest.raises(ToolError, match="AMBIGUOUS_TARGET") as error:
        service.export("张三", output)
    assert len(error.value.details["candidates"]) == 2
    windows.running = True
    with pytest.raises(ToolError, match="NEED_EXIT"):
        service.export("wxid_peer", output)
    assert not output.exists()


@pytest.mark.parametrize("suffix, code", [("-wal", "INVALID_WAL"), ("-journal", "NEED_CLEAN_EXIT")])
def test_residual_transaction_log_blocks_export(pipeline, suffix, code):
    service, _, root, output, _ = pipeline
    (root / ("message/message_0.db" + suffix)).write_bytes(b"uncheckpointed transactions")
    with pytest.raises(ToolError, match=code):
        service.export("wxid_peer", output)
    assert not output.exists()
    assert not list(service.state.work_root().iterdir())


def test_new_shard_and_wrong_self_id_never_silently_omit(pipeline):
    service, _, root, output, _ = pipeline
    with pytest.raises(ToolError, match="SENDER_UNRESOLVED"):
        service.export("wxid_peer", output, self_id="wxid_wrong")
    (root / "message/message_2.db").write_bytes((root / "message/message_0.db").read_bytes())
    with pytest.raises(ToolError, match="KEY_NOT_FOUND"):
        service.export("wxid_peer", output)
    assert not output.exists()


def test_cached_prepare_revalidates_and_contacts_searches(pipeline):
    service, windows, root, _, _ = pipeline
    service.prepare()
    assert windows.acquisitions == 1
    result = service.contacts("张三")
    assert {c["user_id"] for c in result["contacts"]} == {"wxid_peer", "wxid_other"}
    assert all(key.secret.hex().encode() not in service.state.cache_path(root).read_bytes()
               for key in windows.keys.values())


def test_cli_json_and_no_secrets_in_errors(pipeline, capsys):
    service, _, _, output, _ = pipeline
    status = main(["export", "--target", "张三", "--out", str(output), "--json"], lambda args: service)
    captured = capsys.readouterr()
    assert status == 2
    assert json.loads(captured.out)["code"] == "AMBIGUOUS_TARGET"
    assert "Traceback" not in captured.err


def test_modified_existing_export_is_preserved(pipeline):
    service, _, _, output, _ = pipeline
    result = service.export("wxid_peer", output)
    path = Path(result["output"]) / "messages.txt"
    path.write_text("user edited content")
    with pytest.raises(ToolError, match="OUTPUT_EXISTS"):
        service.export("wxid_peer", output)
    assert path.read_text() == "user edited content"


@pytest.mark.parametrize("malformed_schema", [False, True])
def test_bad_new_data_preserves_previous_export(pipeline, native, malformed_schema):
    service, windows, root, output, _ = pipeline
    previous = Path(service.export("wxid_peer", output)["output"])
    original = (previous / "messages.jsonl").read_bytes()
    candidate = root / "message" / "candidate.db"
    table = message_table("wxid_peer")
    schema = "CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY); INSERT INTO Name2Id VALUES('wxid_peer');"
    if malformed_schema:
        schema += f"CREATE TABLE {table}(local_id INTEGER);"
    else:
        schema += f"""CREATE TABLE {table}(local_id INTEGER PRIMARY KEY, local_type INTEGER,
            real_sender_id INTEGER, create_time INTEGER, message_content BLOB);
            INSERT INTO {table} VALUES(1,1,1,1700000001,x'ff');"""
    key = native.create(candidate, schema, b"r" * 32)
    candidate.replace(root / "message/message_0.db")
    windows.keys["message/message_0.db"] = key
    service.state.save_keys(root, windows.keys, {})
    expected = "UNSUPPORTED_SCHEMA" if malformed_schema else "TEXT_DECODE_FAILED"
    with pytest.raises(ToolError, match=expected):
        service.export("wxid_peer", output)
    assert (previous / "messages.jsonl").read_bytes() == original
    assert not list(service.state.work_root().iterdir())


@pytest.mark.skipif(sys.platform == "win32", reason="WSL/Linux behavior")
def test_linux_cli_stops_with_windows_instructions(capsys):
    assert main(["doctor", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "WINDOWS_REQUIRED"
