from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse


REQUIRED_FIELDS = {
    "成品图片": 17,
    "生成时间": 1,
    "查询码": 1,
    "模板版本": 1,
    "生成批次": 1,
    "去重键": 1,
    "上传状态": 1,
}


class FeishuSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeishuTarget:
    app_token: str
    table_id: str
    view_id: str = ""
    primary_field: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "app_token": self.app_token,
            "table_id": self.table_id,
            "view_id": self.view_id,
            "primary_field": self.primary_field,
        }


@dataclass(frozen=True)
class SyncCandidate:
    nickname: str
    normalized_name: str
    avatar_hash: str
    name_hash: str
    output_path: Path
    output_file: str
    created_at: str
    batch_name: str
    template_name: str
    entry_type: str = "follower"
    content_key: str = ""

    @property
    def dedup_key(self) -> str:
        return make_dedup_key(
            self.normalized_name,
            self.avatar_hash,
            self.name_hash,
            self.template_name,
            self.entry_type,
            self.content_key,
        )

    @property
    def legacy_dedup_key(self) -> str:
        if self.entry_type != "follower":
            return ""
        return make_dedup_key(
            self.normalized_name,
            self.avatar_hash,
            self.name_hash,
        )

    @property
    def query_code(self) -> str:
        return self.dedup_key[:8].upper()


def parse_base_url(value: str) -> FeishuTarget:
    raw = value.strip().strip("<>")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc.endswith(
        ".feishu.cn"
    ):
        raise FeishuSyncError("请输入完整的飞书多维表格网址。")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] != "base":
        raise FeishuSyncError("网址不是普通飞书多维表格链接，路径中缺少 /base/。")
    query = parse_qs(parsed.query)
    table_id = (query.get("table") or [""])[0]
    if not table_id:
        raise FeishuSyncError("网址中没有 table 参数，请进入目标数据表后重新复制网址。")
    return FeishuTarget(
        app_token=parts[1],
        table_id=table_id,
        view_id=(query.get("view") or [""])[0],
    )


def save_target(path: Path, target: FeishuTarget) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(target.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_target(path: Path) -> FeishuTarget:
    if not path.exists():
        raise FeishuSyncError("尚未配置飞书。请先双击 06_配置飞书.bat。")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return FeishuTarget(
        app_token=str(payload.get("app_token", "")),
        table_id=str(payload.get("table_id", "")),
        view_id=str(payload.get("view_id", "")),
        primary_field=str(payload.get("primary_field", "")),
    )


def read_credentials() -> tuple[str, str]:
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    missing = [
        name
        for name, value in (
            ("FEISHU_APP_ID", app_id),
            ("FEISHU_APP_SECRET", app_secret),
        )
        if not value
    ]
    if missing:
        raise FeishuSyncError(
            "当前窗口没有读取到环境变量："
            + "、".join(missing)
            + "。设置后请重新打开终端或重新启动 Codex。"
        )
    return app_id, app_secret


def make_dedup_key(
    normalized_name: str,
    avatar_hash: str,
    name_hash: str,
    template_name: str = "",
    entry_type: str = "follower",
    content_key: str = "",
) -> str:
    if entry_type == "comment":
        material_text = (
            f"v3|comment|{template_name.strip().casefold()}|"
            f"{normalized_name}|{avatar_hash}|{content_key}"
        )
    elif template_name:
        material_text = (
            f"v2|{template_name.strip().casefold()}|"
            f"{normalized_name}|{avatar_hash}|{name_hash}"
        )
    else:
        material_text = f"{normalized_name}|{avatar_hash}|{name_hash}"
    material = material_text.encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


def make_client_token(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()[:16]
    return str(uuid.UUID(bytes=digest, version=4))


def _batch_metadata(output_path: Path) -> tuple[str, str]:
    batch_root = output_path.parent.parent
    report_path = batch_root / "report.json"
    template_name = ""
    if report_path.exists():
        try:
            template_name = str(
                json.loads(report_path.read_text(encoding="utf-8")).get(
                    "template", ""
                )
            )
        except (OSError, ValueError, TypeError):
            template_name = ""
    return batch_root.name, template_name


def collect_candidates(root: Path, database_path: Path) -> list[SyncCandidate]:
    if not database_path.exists():
        return []
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(processed_entries)"
            ).fetchall()
        }
        entry_type_column = (
            "entry_type" if "entry_type" in columns else "'follower' AS entry_type"
        )
        content_key_column = (
            "content_key" if "content_key" in columns else "'' AS content_key"
        )
        rows = connection.execute(
            f"""
            SELECT nickname, normalized_name, avatar_hash, name_hash,
                   output_file, created_at, {entry_type_column},
                   {content_key_column}
            FROM processed_entries
            WHERE review_required = 0
            ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()
    candidates: list[SyncCandidate] = []
    for row in rows:
        output_path = root / row["output_file"]
        if not output_path.is_file():
            continue
        batch_name, template_name = _batch_metadata(output_path)
        candidates.append(
            SyncCandidate(
                nickname=row["nickname"],
                normalized_name=row["normalized_name"],
                avatar_hash=row["avatar_hash"],
                name_hash=row["name_hash"],
                output_path=output_path,
                output_file=row["output_file"],
                created_at=row["created_at"],
                batch_name=batch_name,
                template_name=template_name,
                entry_type=row["entry_type"],
                content_key=row["content_key"],
            )
        )
    return candidates


def _plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        pieces: list[str] = []
        for item in value:
            if isinstance(item, dict):
                pieces.append(str(item.get("text", "")))
            else:
                pieces.append(str(item))
        return "".join(pieces)
    if value is None:
        return ""
    return str(value)


class SyncState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feishu_sync (
                dedup_key TEXT PRIMARY KEY,
                output_file TEXT NOT NULL,
                file_token TEXT NOT NULL DEFAULT '',
                record_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def get(self, dedup_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM feishu_sync WHERE dedup_key = ?", (dedup_key,)
        ).fetchone()

    def save(
        self,
        dedup_key: str,
        output_file: str,
        status: str,
        file_token: str = "",
        record_id: str = "",
        last_error: str = "",
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO feishu_sync (
                dedup_key, output_file, file_token, record_id,
                status, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedup_key) DO UPDATE SET
                output_file=excluded.output_file,
                file_token=excluded.file_token,
                record_id=excluded.record_id,
                status=excluded.status,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
            """,
            (
                dedup_key,
                output_file,
                file_token,
                record_id,
                status,
                last_error[:500],
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.connection.commit()


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, target: FeishuTarget) -> None:
        try:
            import lark_oapi as lark
            from lark_oapi.api.bitable.v1 import (
                AppTableField,
                AppTableRecord,
                CreateAppTableFieldRequest,
                CreateAppTableRecordRequest,
                ListAppTableFieldRequest,
                ListAppTableRecordRequest,
            )
            from lark_oapi.api.drive.v1 import (
                UploadAllMediaRequest,
                UploadAllMediaRequestBody,
            )
        except ImportError as exc:
            raise FeishuSyncError(
                "缺少飞书官方 SDK，请重新运行 00_安装环境.bat。"
            ) from exc
        self.lark = lark
        self.AppTableField = AppTableField
        self.AppTableRecord = AppTableRecord
        self.CreateAppTableFieldRequest = CreateAppTableFieldRequest
        self.CreateAppTableRecordRequest = CreateAppTableRecordRequest
        self.ListAppTableFieldRequest = ListAppTableFieldRequest
        self.ListAppTableRecordRequest = ListAppTableRecordRequest
        self.UploadAllMediaRequest = UploadAllMediaRequest
        self.UploadAllMediaRequestBody = UploadAllMediaRequestBody
        self.target = target
        self.client = (
            lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        )

    @staticmethod
    def _error(operation: str, response: Any) -> FeishuSyncError:
        log_id = response.get_log_id() if hasattr(response, "get_log_id") else ""
        return FeishuSyncError(
            f"{operation}失败：code={response.code}，msg={response.msg}，log_id={log_id}"
        )

    def list_fields(self) -> list[Any]:
        items: list[Any] = []
        page_token = ""
        while True:
            builder = (
                self.ListAppTableFieldRequest.builder()
                .app_token(self.target.app_token)
                .table_id(self.target.table_id)
                .page_size(100)
            )
            if page_token:
                builder = builder.page_token(page_token)
            response = self.client.bitable.v1.app_table_field.list(builder.build())
            if not response.success():
                raise self._error("读取表格字段", response)
            items.extend(response.data.items or [])
            if not response.data.has_more:
                break
            page_token = response.data.page_token
        return items

    def ensure_schema(self) -> str:
        fields = self.list_fields()
        primary = next((field for field in fields if field.is_primary), None)
        if primary is None:
            raise FeishuSyncError("目标数据表没有主字段，无法写入昵称。")
        existing = {field.field_name for field in fields}
        for field_name, field_type in REQUIRED_FIELDS.items():
            if field_name in existing:
                continue
            request = (
                self.CreateAppTableFieldRequest.builder()
                .app_token(self.target.app_token)
                .table_id(self.target.table_id)
                .client_token(
                    make_client_token(
                        f"field:{self.target.app_token}:{self.target.table_id}:{field_name}"
                    )
                )
                .request_body(
                    self.AppTableField.builder()
                    .field_name(field_name)
                    .type(field_type)
                    .build()
                )
                .build()
            )
            response = self.client.bitable.v1.app_table_field.create(request)
            if not response.success():
                raise self._error(f"创建字段“{field_name}”", response)
        return primary.field_name

    def remote_dedup_entries(self) -> set[tuple[str, str]]:
        values: set[tuple[str, str]] = set()
        page_token = ""
        while True:
            builder = (
                self.ListAppTableRecordRequest.builder()
                .app_token(self.target.app_token)
                .table_id(self.target.table_id)
                .page_size(500)
            )
            if page_token:
                builder = builder.page_token(page_token)
            response = self.client.bitable.v1.app_table_record.list(builder.build())
            if not response.success():
                raise self._error("读取远端去重记录", response)
            for item in response.data.items or []:
                fields = item.fields or {}
                value = _plain_text(fields.get("去重键"))
                template_name = _plain_text(fields.get("模板版本"))
                if value:
                    values.add((value, template_name))
            if not response.data.has_more:
                break
            page_token = response.data.page_token
        return values

    def upload_image(self, path: Path) -> str:
        with path.open("rb") as handle:
            body = (
                self.UploadAllMediaRequestBody.builder()
                .file_name(path.name)
                .parent_type("bitable_image")
                .parent_node(self.target.app_token)
                .size(path.stat().st_size)
                .file(handle)
                .build()
            )
            request = self.UploadAllMediaRequest.builder().request_body(body).build()
            response = self.client.drive.v1.media.upload_all(request)
        if not response.success():
            raise self._error(f"上传图片“{path.name}”", response)
        return response.data.file_token

    def create_record(self, fields: dict[str, Any], client_token: str) -> str:
        request = (
            self.CreateAppTableRecordRequest.builder()
            .app_token(self.target.app_token)
            .table_id(self.target.table_id)
            .client_token(make_client_token(client_token))
            .request_body(self.AppTableRecord.builder().fields(fields).build())
            .build()
        )
        response = self.client.bitable.v1.app_table_record.create(request)
        if not response.success():
            raise self._error("创建多维表格记录", response)
        return response.data.record.record_id


def configure_target(
    target_path: Path,
    url: str,
    app_id: str,
    app_secret: str,
) -> FeishuTarget:
    parsed = parse_base_url(url)
    client = FeishuClient(app_id, app_secret, parsed)
    primary_field = client.ensure_schema()
    configured = FeishuTarget(
        app_token=parsed.app_token,
        table_id=parsed.table_id,
        view_id=parsed.view_id,
        primary_field=primary_field,
    )
    save_target(target_path, configured)
    return configured


def sync_candidates(
    client: FeishuClient,
    candidates: Iterable[SyncCandidate],
    state: SyncState,
) -> dict[str, int]:
    primary_field = client.target.primary_field or client.ensure_schema()
    remote_entries = client.remote_dedup_entries()
    remote_keys = {key for key, _ in remote_entries}
    result = {"uploaded": 0, "skipped": 0, "failed": 0}
    for candidate in candidates:
        key = candidate.dedup_key
        legacy_match = candidate.entry_type == "follower" and (
            candidate.legacy_dedup_key,
            candidate.template_name,
        ) in remote_entries
        if key in remote_keys or legacy_match:
            result["skipped"] += 1
            state.save(key, candidate.output_file, "remote_exists")
            continue
        saved = state.get(key)
        file_token = saved["file_token"] if saved else ""
        try:
            if not file_token:
                file_token = client.upload_image(candidate.output_path)
                state.save(
                    key,
                    candidate.output_file,
                    "media_uploaded",
                    file_token=file_token,
                )
            fields = {
                primary_field: candidate.nickname,
                "成品图片": [{"file_token": file_token}],
                "生成时间": candidate.created_at,
                "查询码": candidate.query_code,
                "模板版本": candidate.template_name,
                "生成批次": candidate.batch_name,
                "去重键": key,
                "上传状态": "已上传",
            }
            record_id = client.create_record(
                fields,
                f"cat-fan:{key}",
            )
        except FeishuSyncError as exc:
            result["failed"] += 1
            state.save(
                key,
                candidate.output_file,
                "failed",
                file_token=file_token,
                last_error=str(exc),
            )
            print(f"上传失败：{candidate.nickname}；{exc}")
            continue
        state.save(
            key,
            candidate.output_file,
            "success",
            file_token=file_token,
            record_id=record_id,
        )
        remote_keys.add(key)
        result["uploaded"] += 1
        print(f"上传成功：{candidate.nickname}（查询码 {candidate.query_code}）")
    return result
