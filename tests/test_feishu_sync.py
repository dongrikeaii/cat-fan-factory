import tempfile
import unittest
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feishu_sync import (
    FeishuSyncError,
    FeishuTarget,
    SyncCandidate,
    SyncState,
    make_client_token,
    make_dedup_key,
    parse_base_url,
    sync_candidates,
)


class FakeFeishuClient:
    def __init__(self, remote_entries=None):
        self.target = FeishuTarget("base-token", "table-id", primary_field="粉丝昵称")
        self.remote_entries = set(remote_entries or [])
        self.uploaded = []
        self.records = []

    def ensure_schema(self):
        return "粉丝昵称"

    def remote_dedup_entries(self):
        return set(self.remote_entries)

    def upload_image(self, path):
        self.uploaded.append(path)
        return "file-token"

    def create_record(self, fields, client_token):
        self.records.append((fields, client_token))
        return "record-id"


class FeishuSyncTests(unittest.TestCase):
    def test_parse_base_url(self):
        target = parse_base_url(
            "https://demo.feishu.cn/base/baseToken?table=tbl123&view=vew456"
        )
        self.assertEqual("baseToken", target.app_token)
        self.assertEqual("tbl123", target.table_id)
        self.assertEqual("vew456", target.view_id)

    def test_parse_rejects_share_or_non_feishu_url(self):
        with self.assertRaises(FeishuSyncError):
            parse_base_url("https://example.com/base/token?table=tbl123")

    def test_dedup_key_is_stable(self):
        first = make_dedup_key("stella", "abc", "def")
        second = make_dedup_key("stella", "abc", "def")
        self.assertEqual(first, second)
        self.assertEqual(32, len(first))
        classic = make_dedup_key("stella", "abc", "def", "classic-cat")
        orange = make_dedup_key("stella", "abc", "def", "Orange Cat")
        self.assertNotEqual(classic, orange)
        first_comment = make_dedup_key(
            "stella", "abc", "def", "Orange Cat", "comment", "first"
        )
        second_comment = make_dedup_key(
            "stella", "abc", "def", "Orange Cat", "comment", "second"
        )
        self.assertNotEqual(first_comment, second_comment)
        self.assertNotEqual(orange, first_comment)

    def test_client_token_is_stable_uuid4(self):
        first = make_client_token("same operation")
        second = make_client_token("same operation")
        self.assertEqual(first, second)
        self.assertEqual(4, uuid.UUID(first).version)

    def test_sync_uploads_once_and_writes_attachment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "fan.jpg"
            image.write_bytes(b"jpeg")
            candidate = SyncCandidate(
                nickname="Stella",
                normalized_name="stella",
                avatar_hash="a" * 16,
                name_hash="b" * 16,
                output_path=image,
                output_file="output/fan.jpg",
                created_at="2026-08-21T22:00:00",
                batch_name="batch",
                template_name="classic-cat",
            )
            client = FakeFeishuClient()
            state = SyncState(root / "state.sqlite3")
            try:
                result = sync_candidates(client, [candidate], state)
            finally:
                state.close()
            self.assertEqual({"uploaded": 1, "skipped": 0, "failed": 0}, result)
            self.assertEqual([image], client.uploaded)
            fields = client.records[0][0]
            self.assertEqual("Stella", fields["粉丝昵称"])
            self.assertEqual([{"file_token": "file-token"}], fields["成品图片"])
            self.assertEqual(candidate.dedup_key, fields["去重键"])

    def test_remote_dedup_skips_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "fan.jpg"
            image.write_bytes(b"jpeg")
            candidate = SyncCandidate(
                nickname="Stella",
                normalized_name="stella",
                avatar_hash="a" * 16,
                name_hash="b" * 16,
                output_path=image,
                output_file="output/fan.jpg",
                created_at="2026-08-21T22:00:00",
                batch_name="batch",
                template_name="classic-cat",
            )
            client = FakeFeishuClient(
                {(candidate.dedup_key, candidate.template_name)}
            )
            state = SyncState(root / "state.sqlite3")
            try:
                result = sync_candidates(client, [candidate], state)
            finally:
                state.close()
            self.assertEqual({"uploaded": 0, "skipped": 1, "failed": 0}, result)
            self.assertEqual([], client.uploaded)

    def test_legacy_key_only_skips_the_same_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "fan.jpg"
            image.write_bytes(b"jpeg")
            classic = SyncCandidate(
                nickname="Stella",
                normalized_name="stella",
                avatar_hash="a" * 16,
                name_hash="b" * 16,
                output_path=image,
                output_file="output/classic.jpg",
                created_at="2026-08-21T22:00:00",
                batch_name="classic-batch",
                template_name="classic-cat",
            )
            orange = SyncCandidate(
                **{
                    **classic.__dict__,
                    "output_file": "output/orange.jpg",
                    "batch_name": "orange-batch",
                    "template_name": "Orange Cat",
                }
            )
            remote = {(classic.legacy_dedup_key, "classic-cat")}
            classic_client = FakeFeishuClient(remote)
            classic_state = SyncState(root / "classic.sqlite3")
            try:
                classic_result = sync_candidates(
                    classic_client, [classic], classic_state
                )
            finally:
                classic_state.close()
            self.assertEqual(1, classic_result["skipped"])

            orange_client = FakeFeishuClient(remote)
            orange_state = SyncState(root / "orange.sqlite3")
            try:
                orange_result = sync_candidates(
                    orange_client, [orange], orange_state
                )
            finally:
                orange_state.close()
            self.assertEqual(1, orange_result["uploaded"])


if __name__ == "__main__":
    unittest.main()
