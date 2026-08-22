from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps
from rapidocr_onnxruntime import RapidOCR

from feishu_sync import (
    FeishuClient,
    FeishuSyncError,
    SyncState,
    collect_candidates,
    configure_target,
    load_target,
    read_credentials,
    sync_candidates,
)


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
FEISHU_TARGET_PATH = ROOT / "data" / "feishu_config.json"
FEISHU_STATE_PATH = ROOT / "data" / "feishu_sync.sqlite3"


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def project_path(value: str) -> Path:
    return ROOT / Path(value)


def ensure_directories(config: dict[str, Any]) -> None:
    for key in ("inbox", "output_batches", "data", "templates"):
        project_path(config["paths"][key]).mkdir(parents=True, exist_ok=True)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{stamp}_{counter}{path.suffix}")
        counter += 1
    return candidate


def safe_filename(value: str, fallback: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value).strip(" ._")
    return (value[:48] or fallback).strip(" .")


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def image_dhash(image: Image.Image, size: int = 8) -> str:
    gray = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.int16)
    bits = pixels[:, 1:] > pixels[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return f"{value:0{size * size // 4}x}"


def hash_distance(first: str, second: str) -> int:
    return (int(first, 16) ^ int(second, 16)).bit_count()


@dataclass
class DetectedRow:
    index: int
    top: int
    bottom: int
    center: float
    partial: bool


@dataclass
class OcrItem:
    text: str
    confidence: float
    left: float
    top: float
    right: float
    bottom: float


@dataclass
class RowAnalysis:
    nickname: str
    normalized_name: str
    nickname_confidence: float
    follow_marker_found: bool
    ocr_text: list[str]
    avatar_hash: str
    name_hash: str
    needs_review: bool
    review_reasons: list[str]


@dataclass(frozen=True)
class TemplateBundle:
    name: str
    directory: Path
    base: Path
    paw_overlay: Path
    mask_debug: Path
    options: dict[str, Any]


@dataclass(frozen=True)
class BatchPaths:
    root: Path
    final: Path
    crops: Path
    review: Path
    sources: Path


class Database:
    def __init__(self, path: Path, root: Path = ROOT) -> None:
        self.root = root
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                avatar_hash TEXT NOT NULL,
                name_hash TEXT NOT NULL,
                source_file TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                ocr_confidence REAL NOT NULL,
                review_required INTEGER NOT NULL,
                template_name TEXT NOT NULL DEFAULT '',
                output_file TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(processed_entries)"
            ).fetchall()
        }
        if "template_name" not in columns:
            self.connection.execute(
                "ALTER TABLE processed_entries "
                "ADD COLUMN template_name TEXT NOT NULL DEFAULT ''"
            )
        self._migrate_template_names()
        self.connection.commit()

    def _migrate_template_names(self) -> None:
        rows = self.connection.execute(
            """
            SELECT id, output_file FROM processed_entries
            WHERE template_name = ''
            """
        ).fetchall()
        for row in rows:
            output_path = self.root / row["output_file"]
            report_path = output_path.parent.parent / "report.json"
            if not report_path.is_file():
                continue
            try:
                template_name = str(
                    json.loads(report_path.read_text(encoding="utf-8")).get(
                        "template", ""
                    )
                ).strip()
            except (OSError, ValueError, TypeError):
                continue
            if template_name:
                self.connection.execute(
                    "UPDATE processed_entries SET template_name = ? WHERE id = ?",
                    (template_name, row["id"]),
                )

    def find_duplicate(
        self, analysis: RowAnalysis, template_name: str
    ) -> sqlite3.Row | None:
        rows = self.connection.execute(
            """
            SELECT * FROM processed_entries
            WHERE template_name = ? AND review_required = 0
            ORDER BY id DESC
            """,
            (template_name,),
        ).fetchall()
        for row in rows:
            avatar_distance = hash_distance(analysis.avatar_hash, row["avatar_hash"])
            name_distance = hash_distance(analysis.name_hash, row["name_hash"])
            same_name = bool(
                analysis.normalized_name
                and analysis.normalized_name == row["normalized_name"]
            )
            if same_name and avatar_distance <= 12:
                return row
            if avatar_distance <= 5 and name_distance <= 6:
                return row
        return None

    def insert(
        self,
        analysis: RowAnalysis,
        source_file: str,
        row_index: int,
        output_file: str,
        template_name: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO processed_entries (
                nickname, normalized_name, avatar_hash, name_hash, source_file,
                row_index, ocr_confidence, review_required, template_name,
                output_file, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis.nickname,
                analysis.normalized_name,
                analysis.avatar_hash,
                analysis.name_hash,
                source_file,
                row_index,
                analysis.nickname_confidence,
                int(analysis.needs_review),
                template_name,
                output_file,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.connection.commit()

    def counts(self) -> tuple[int, int]:
        total = self.connection.execute(
            "SELECT COUNT(*) FROM processed_entries"
        ).fetchone()[0]
        review = self.connection.execute(
            "SELECT COUNT(*) FROM processed_entries WHERE review_required = 1"
        ).fetchone()[0]
        return total, review

    def export_csv(self, path: Path) -> None:
        rows = self.connection.execute(
            "SELECT * FROM processed_entries ORDER BY id"
        ).fetchall()
        if not rows:
            return
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(rows[0].keys())
            writer.writerows(tuple(row) for row in rows)


class OcrReader:
    def __init__(self) -> None:
        self.engine = RapidOCR()

    def read(self, image: Image.Image) -> list[OcrItem]:
        rgb = np.asarray(image.convert("RGB"))
        result, _ = self.engine(rgb)
        items: list[OcrItem] = []
        for raw in result or []:
            box, text, confidence = raw
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            items.append(
                OcrItem(
                    text=str(text).strip(),
                    confidence=float(confidence),
                    left=min(xs),
                    top=min(ys),
                    right=max(xs),
                    bottom=max(ys),
                )
            )
        return sorted(items, key=lambda item: (item.top, item.left))


def detect_ellipsis_markers(
    rgb: np.ndarray,
) -> list[tuple[float, tuple[int, int]]]:
    height, width = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    x_grid = np.indices(gray.shape)[1]
    dark_right = ((gray < 100) & (x_grid >= width * 0.88)).astype(np.uint8)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(dark_right, 8)
    max_dot_size = max(8, int(round(width * 0.016)))
    dots: list[tuple[float, float, int, int, int]] = []
    for label in range(1, count):
        x, y, component_width, component_height, area = stats[label]
        if not 2 <= component_width <= max_dot_size:
            continue
        if not 2 <= component_height <= max_dot_size:
            continue
        if not 4 <= area <= max_dot_size * max_dot_size:
            continue
        aspect = component_width / component_height
        if not 0.45 <= aspect <= 2.2:
            continue
        dots.append(
            (
                float(centroids[label][0]),
                float(centroids[label][1]),
                int(y),
                int(y + component_height),
                int(area),
            )
        )

    y_tolerance = max(2.0, min(7.0, height * 0.004))
    groups: list[list[tuple[float, float, int, int, int]]] = []
    for dot in sorted(dots, key=lambda item: (item[1], item[0])):
        matching = next(
            (
                group
                for group in groups
                if abs(dot[1] - float(np.mean([item[1] for item in group])))
                <= y_tolerance
            ),
            None,
        )
        if matching is None:
            groups.append([dot])
        else:
            matching.append(dot)

    markers: list[tuple[float, tuple[int, int]]] = []
    for group in groups:
        ordered = sorted(group, key=lambda item: item[0])
        for start in range(len(ordered) - 2):
            triple = ordered[start : start + 3]
            gaps = [triple[1][0] - triple[0][0], triple[2][0] - triple[1][0]]
            if min(gaps) < width * 0.008 or max(gaps) > width * 0.025:
                continue
            if max(gaps) / min(gaps) > 1.7:
                continue
            areas = [item[4] for item in triple]
            if max(areas) / min(areas) > 3:
                continue
            center = float(np.mean([item[1] for item in triple]))
            markers.append(
                (center, (min(item[2] for item in triple), max(item[3] for item in triple)))
            )
            break
    return sorted(markers, key=lambda item: item[0])


def detect_rows(image: Image.Image, config: dict[str, Any]) -> list[DetectedRow]:
    options = config["row_detection"]
    rgb = np.asarray(image.convert("RGB"))
    width, height = image.size
    ellipsis_markers = detect_ellipsis_markers(rgb)
    centers = [item[0] for item in ellipsis_markers]
    component_bounds = [item[1] for item in ellipsis_markers]
    if not centers:
        red = (
            (rgb[:, :, 0] >= options["red_r_min"])
            & (rgb[:, :, 1] <= options["red_g_max"])
            & (rgb[:, :, 2] <= options["red_b_max"])
            & (
                (rgb[:, :, 0].astype(np.int16) - rgb[:, :, 1])
                >= options["red_gap_min"]
            )
        ).astype(np.uint8)
        count, _, stats, centroids = cv2.connectedComponentsWithStats(red, 8)
        for label in range(1, count):
            x, y, component_width, component_height, area = stats[label]
            if x < width * options["min_x_ratio"]:
                continue
            if component_width < width * options["min_width_ratio"]:
                continue
            if component_height < max(20, height * options["min_height_ratio"]):
                continue
            if area < options["min_area"]:
                continue
            centers.append(float(centroids[label][1]))
            component_bounds.append((int(y), int(y + component_height)))

    if not centers:
        if width >= 500 and width / max(height, 1) >= 3:
            return [DetectedRow(1, 0, height, height / 2, False)]
        return []
    if len(centers) == 1 and width >= 500 and width / max(height, 1) >= 3:
        return [DetectedRow(1, 0, height, height / 2, False)]
    ordering = np.argsort(centers)
    centers = [centers[index] for index in ordering]
    component_bounds = [component_bounds[index] for index in ordering]
    gaps = [b - a for a, b in zip(centers, centers[1:]) if b - a > 40]
    if gaps:
        row_height = float(np.median(gaps))
    else:
        button_height = component_bounds[0][1] - component_bounds[0][0]
        row_height = max(button_height * 2.8, height * 0.09)
    row_height = min(max(row_height, height * 0.07), height * 0.16)

    rows: list[DetectedRow] = []
    for index, (center, bounds) in enumerate(zip(centers, component_bounds), start=1):
        intended_top = int(round(center - row_height / 2))
        intended_bottom = int(round(center + row_height / 2))
        top = max(0, intended_top)
        bottom = min(height, intended_bottom)
        component_clipped = bounds[0] <= 1 or bounds[1] >= height - 1
        partial = component_clipped or (bottom - top) < row_height * 0.85
        rows.append(DetectedRow(index, top, bottom, center, partial))
    return rows


def ignored_ocr_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return True
    ignored_fragments = (
        "关注了你",
        "回关",
        "互相关注",
        "互动消息",
        "新关注我的",
        "分钟前",
        "小时前",
        "天前",
    )
    if any(fragment in compact for fragment in ignored_fragments):
        return True
    return bool(re.fullmatch(r"[\d:]+", compact))


def analyze_row(
    row_image: Image.Image,
    items: list[OcrItem],
    partial: bool,
    config: dict[str, Any],
) -> RowAnalysis:
    width, height = row_image.size
    follow_marker = any(
        any(
            marker in item.text.replace(" ", "")
            for marker in ("关注了你", "回关", "互相关注")
        )
        for item in items
    )
    candidates = [
        item
        for item in items
        if item.left >= width * 0.15
        and item.left <= width * 0.72
        and (item.top + item.bottom) / 2 <= height * 0.55
        and not ignored_ocr_text(item.text)
    ]
    candidates.sort(key=lambda item: (item.top, -item.confidence, item.left))
    nickname_item = candidates[0] if candidates else None
    nickname = nickname_item.text.strip() if nickname_item else ""
    confidence = nickname_item.confidence if nickname_item else 0.0

    avatar = row_image.crop(
        (int(width * 0.015), int(height * 0.08), int(width * 0.19), int(height * 0.92))
    )
    name_region = row_image.crop(
        (int(width * 0.18), 0, int(width * 0.70), int(height * 0.62))
    )
    reasons: list[str] = []
    if not nickname:
        reasons.append("昵称未识别，可能是纯表情昵称")
    if nickname and confidence < config["ocr"]["minimum_name_confidence"]:
        reasons.append(f"昵称OCR置信度较低：{confidence:.3f}")
    if not follow_marker:
        reasons.append("未识别到“关注了你”“回关”或“互相关注”标记")
    if partial:
        reasons.append("该条目位于截图边缘，内容可能不完整")

    return RowAnalysis(
        nickname=nickname or "昵称待确认",
        normalized_name=normalize_name(nickname),
        nickname_confidence=confidence,
        follow_marker_found=follow_marker,
        ocr_text=[item.text for item in items],
        avatar_hash=image_dhash(avatar),
        name_hash=image_dhash(name_region),
        needs_review=bool(reasons),
        review_reasons=reasons,
    )


def template_directories(config: dict[str, Any]) -> dict[str, Path]:
    root = project_path(config["paths"]["templates"])
    root.mkdir(parents=True, exist_ok=True)
    return {
        path.name: path
        for path in sorted(root.iterdir(), key=lambda item: item.name.casefold())
        if path.is_dir()
        and (path / "cat_base.png").is_file()
        and (path / "paw_foreground.png").is_file()
    }


def load_template_bundle(
    config: dict[str, Any], name: str, directory: Path
) -> TemplateBundle:
    options = dict(config["template_defaults"])
    template_config = directory / "template.json"
    if template_config.exists():
        with template_config.open("r", encoding="utf-8") as handle:
            overrides = json.load(handle)
        unknown = set(overrides) - set(options)
        if unknown:
            raise ValueError(
                f"模板 {name} 的 template.json 包含未知字段：{', '.join(sorted(unknown))}"
            )
        options.update(overrides)
    return TemplateBundle(
        name=name,
        directory=directory,
        base=directory / "cat_base.png",
        paw_overlay=directory / "paw_foreground.png",
        mask_debug=directory / "paw_mask_debug.png",
        options=options,
    )


def active_template_name(config: dict[str, Any]) -> str:
    active_file = project_path(config["paths"]["active_template"])
    if active_file.exists():
        selected = active_file.read_text(encoding="utf-8").strip()
        if selected:
            return selected
    return str(config["default_template"])


def resolve_template(
    config: dict[str, Any], name: str | None = None
) -> TemplateBundle:
    templates = template_directories(config)
    if not templates:
        raise FileNotFoundError(
            "templates 中没有可用版本。每个版本文件夹必须包含 "
            "cat_base.png 和 paw_foreground.png。"
        )
    selected = name or active_template_name(config)
    if selected not in templates:
        available = "、".join(templates)
        raise ValueError(f"模板不存在：{selected}。可用模板：{available}")
    return load_template_bundle(config, selected, templates[selected])


def prepare_template_mask(template: TemplateBundle) -> dict[str, Any]:
    with Image.open(template.base) as base, Image.open(template.paw_overlay) as paw:
        if "A" not in paw.getbands():
            raise ValueError(
                f"{template.name}/paw_foreground.png 没有透明通道，请保存为 RGBA PNG。"
            )
        if base.size != paw.size:
            raise ValueError(
                f"模板 {template.name} 尺寸不一致：cat_base={base.size}, "
                f"paw_foreground={paw.size}"
            )
        size = base.size
        alpha = paw.getchannel("A")
    alpha.save(template.mask_debug, optimize=True)
    minimum, maximum = alpha.getextrema()
    return {
        "template": template.name,
        "size": list(size),
        "alpha_min": minimum,
        "alpha_max": maximum,
        "mask": str(template.mask_debug.relative_to(ROOT)),
    }


def prepare_templates(
    config: dict[str, Any], name: str | None = None
) -> list[dict[str, Any]]:
    if name:
        bundles = [resolve_template(config, name)]
    else:
        bundles = [
            load_template_bundle(config, item_name, directory)
            for item_name, directory in template_directories(config).items()
        ]
    if not bundles:
        raise FileNotFoundError("没有找到可生成蒙版的模板文件夹。")
    results = [prepare_template_mask(bundle) for bundle in bundles]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def ensure_template_ready(template: TemplateBundle) -> None:
    if (
        not template.mask_debug.exists()
        or template.mask_debug.stat().st_mtime < template.paw_overlay.stat().st_mtime
    ):
        prepare_template_mask(template)


def set_active_template(config: dict[str, Any], name: str) -> TemplateBundle:
    template = resolve_template(config, name)
    prepare_template_mask(template)
    active_file = project_path(config["paths"]["active_template"])
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_text(f"{name}\n", encoding="utf-8")
    print(f"当前模板已切换为：{name}")
    return template


def choose_template(config: dict[str, Any]) -> None:
    names = list(template_directories(config))
    if not names:
        raise FileNotFoundError("没有可切换的模板。")
    current = active_template_name(config)
    print("可用模板：")
    for index, name in enumerate(names, start=1):
        marker = "（当前）" if name == current else ""
        print(f"  {index}. {name}{marker}")
    choice = input("请输入模板序号或文件夹名称：").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(names):
        choice = names[int(choice) - 1]
    set_active_template(config, choice)


def list_templates(config: dict[str, Any]) -> None:
    current = active_template_name(config)
    payload = [
        {"name": name, "active": name == current}
        for name in template_directories(config)
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def compose_card(row_image: Image.Image, template: TemplateBundle) -> Image.Image:
    options = template.options
    base = Image.open(template.base).convert("RGBA")
    paw = Image.open(template.paw_overlay).convert("RGBA")
    if paw.size != base.size:
        raise ValueError("猫爪前景与猫图底层尺寸不一致，请重建模板。")

    card_width = int(base.width * options["card_width_ratio"])
    card_height = max(1, int(row_image.height * card_width / row_image.width))
    card = row_image.convert("RGB").resize(
        (card_width, card_height), Image.Resampling.LANCZOS
    )
    card = ImageOps.expand(card, border=2, fill=(225, 225, 228)).convert("RGBA")
    rotated = card.rotate(
        options["angle_degrees"],
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )
    center_x = int(base.width * options["center_x_ratio"])
    center_y = int(base.height * options["center_y_ratio"])
    left = center_x - rotated.width // 2
    top = center_y - rotated.height // 2

    shadow_alpha = rotated.getchannel("A").filter(
        ImageFilter.GaussianBlur(options["shadow_blur"])
    )
    shadow_alpha = shadow_alpha.point(
        lambda value: int(value * options["shadow_opacity"])
    )
    shadow = Image.new("RGBA", rotated.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_alpha)
    base.alpha_composite(
        shadow,
        (
            left + options["shadow_offset_x"],
            top + options["shadow_offset_y"],
        ),
    )
    base.alpha_composite(rotated, (left, top))
    base.alpha_composite(paw)
    return base.convert("RGB")


def save_review_metadata(
    path: Path,
    source: Path,
    row: DetectedRow,
    analysis: RowAnalysis,
) -> None:
    payload = {
        "source": source.name,
        "row_index": row.index,
        "nickname": analysis.nickname,
        "ocr_confidence": round(analysis.nickname_confidence, 4),
        "ocr_text": analysis.ocr_text,
        "review_reasons": analysis.review_reasons,
    }
    path.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def process_screenshot(
    path: Path,
    config: dict[str, Any],
    database: Database,
    ocr: OcrReader,
    template: TemplateBundle,
    batch: BatchPaths,
) -> dict[str, Any]:
    image = Image.open(path).convert("RGB")
    rows = detect_rows(image, config)
    report: dict[str, Any] = {
        "source": path.name,
        "template": template.name,
        "detected": len(rows),
        "generated": 0,
        "duplicates": 0,
        "needs_review": 0,
        "items": [],
    }
    if not rows:
        destination = unique_path(batch.sources / path.name)
        shutil.move(str(path), destination)
        report["error"] = "没有检测到关注条目，原图已保存在本批次的 source_screenshots。"
        return report

    for row in rows:
        row_image = image.crop((0, row.top, image.width, row.bottom))
        crop_name = f"{path.stem}_row_{row.index:02d}.png"
        crop_path = batch.crops / crop_name
        row_image.save(crop_path, optimize=True)
        items = ocr.read(row_image)
        analysis = analyze_row(row_image, items, row.partial, config)
        duplicate = database.find_duplicate(analysis, template.name)
        item_report = {
            "row": row.index,
            "nickname": analysis.nickname,
            "ocr_confidence": round(analysis.nickname_confidence, 4),
            "duplicate": bool(duplicate),
            "review": analysis.needs_review,
            "reasons": analysis.review_reasons,
        }
        if duplicate:
            report["duplicates"] += 1
            item_report["duplicate_of"] = duplicate["output_file"]
            report["items"].append(item_report)
            continue

        output_root = batch.review if analysis.needs_review else batch.final
        nickname_file = safe_filename(analysis.nickname, f"row_{row.index:02d}")
        output_name = (
            f"{path.stem}_{row.index:02d}_{nickname_file}_{analysis.avatar_hash[:8]}.jpg"
        )
        output_path = unique_path(output_root / output_name)
        result = compose_card(row_image, template)
        result.save(
            output_path,
            quality=config["output"]["jpeg_quality"],
            subsampling=0,
            optimize=True,
        )
        database.insert(
            analysis,
            source_file=path.name,
            row_index=row.index,
            output_file=str(output_path.relative_to(ROOT)),
            template_name=template.name,
        )
        if analysis.needs_review:
            report["needs_review"] += 1
            save_review_metadata(output_path, path, row, analysis)
        else:
            report["generated"] += 1
        item_report["output"] = str(output_path.relative_to(ROOT))
        report["items"].append(item_report)

    archived = unique_path(batch.sources / path.name)
    shutil.move(str(path), archived)
    report["archived_to"] = str(archived.relative_to(ROOT))
    return report


def inbox_images(config: dict[str, Any]) -> Iterable[Path]:
    inbox = project_path(config["paths"]["inbox"])
    return sorted(
        path
        for path in inbox.iterdir()
        if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
    )


def wait_until_stable(path: Path, delay: float = 0.8) -> bool:
    try:
        first = path.stat().st_size
        time.sleep(delay)
        return path.exists() and path.stat().st_size == first and first > 0
    except OSError:
        return False


def create_batch_paths(config: dict[str, Any]) -> BatchPaths:
    root = project_path(config["paths"]["output_batches"])
    root.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    candidate = root / name
    counter = 2
    while candidate.exists():
        candidate = root / f"{name}_{counter:02d}"
        counter += 1
    batch = BatchPaths(
        root=candidate,
        final=candidate / "final",
        crops=candidate / "cropped_rows",
        review=candidate / "needs_review",
        sources=candidate / "source_screenshots",
    )
    for item in (batch.final, batch.crops, batch.review, batch.sources):
        item.mkdir(parents=True, exist_ok=True)
    return batch


def process_inbox(config: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_directories(config)
    database = Database(project_path(config["paths"]["database"]))
    candidates = list(inbox_images(config))
    if not candidates:
        print("待处理文件夹为空。")
        return []
    template = resolve_template(config)
    ensure_template_ready(template)
    batch = create_batch_paths(config)
    ocr = OcrReader()
    reports: list[dict[str, Any]] = []
    for path in candidates:
        if not wait_until_stable(path):
            print(f"文件仍在写入，稍后重试：{path.name}")
            continue
        try:
            report = process_screenshot(path, config, database, ocr, template, batch)
        except Exception as exc:  # keep the watcher alive and preserve the source
            report = {"source": path.name, "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(report, ensure_ascii=False, indent=2))
            reports.append(report)
            continue
        print(json.dumps(report, ensure_ascii=False, indent=2))
        reports.append(report)
    database.export_csv(project_path(config["paths"]["csv_report"]))
    batch_report = {
        "batch": batch.root.name,
        "template": template.name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "screenshots": reports,
    }
    (batch.root / "report.json").write_text(
        json.dumps(batch_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"批次文件夹：{batch.root}")
    return reports


def watch(config: dict[str, Any]) -> None:
    interval = float(config["watch"]["poll_seconds"])
    ensure_directories(config)
    resolve_template(config)
    print(f"正在监听：{project_path(config['paths']['inbox'])}")
    print("把截图放进该文件夹即可；按 Ctrl+C 停止。")
    try:
        while True:
            if any(inbox_images(config)):
                process_inbox(config)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止监听。")


def show_status(config: dict[str, Any]) -> None:
    ensure_directories(config)
    database = Database(project_path(config["paths"]["database"]))
    total, review = database.counts()
    batches = project_path(config["paths"]["output_batches"])
    final_count = len(list(batches.glob("*/final/*.jpg")))
    review_count = len(list(batches.glob("*/needs_review/*.jpg")))
    legacy_final = ROOT / "output" / "final"
    legacy_review = ROOT / "needs_review"
    if legacy_final.exists():
        final_count += len(list(legacy_final.glob("*.jpg")))
    if legacy_review.exists():
        review_count += len(list(legacy_review.glob("*.jpg")))
    status = {
        "当前模板": active_template_name(config),
        "可用模板": list(template_directories(config)),
        "待处理截图": len(list(inbox_images(config))),
        "已记录关注条目": total,
        "其中待复核": review,
        "正式成品": final_count,
        "待复核成品": review_count,
        "批次数量": len([path for path in batches.iterdir() if path.is_dir()]),
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))


def configure_feishu(url: str | None) -> None:
    app_id, app_secret = read_credentials()
    if not url:
        url = input("请粘贴飞书多维表格完整网址：").strip()
    target = configure_target(FEISHU_TARGET_PATH, url, app_id, app_secret)
    print("飞书配置成功，必需字段已经检查并补齐。")
    print(f"昵称将写入主字段：{target.primary_field}")
    print("表格标识只保存在 data/feishu_config.json，不会提交到 GitHub。")


def create_feishu_client() -> FeishuClient:
    target = load_target(FEISHU_TARGET_PATH)
    app_id, app_secret = read_credentials()
    return FeishuClient(app_id, app_secret, target)


def test_feishu_connection() -> None:
    client = create_feishu_client()
    primary_field = client.target.primary_field or client.ensure_schema()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    test_path = ROOT / "data" / "feishu_connection_test.jpg"
    image = Image.new("RGB", (640, 360), (245, 241, 232))
    image.save(test_path, quality=90)
    try:
        file_token = client.upload_image(test_path)
        dedup_key = f"connection-test-{uuid.uuid4().hex[:16]}"
        record_id = client.create_record(
            {
                primary_field: f"连接测试 {stamp}",
                "成品图片": [{"file_token": file_token}],
                "生成时间": stamp,
                "查询码": "TEST",
                "模板版本": "连接测试",
                "生成批次": "连接测试",
                "去重键": dedup_key,
                "上传状态": "连接成功",
            },
            str(uuid.uuid4()),
        )
    finally:
        test_path.unlink(missing_ok=True)
    print(f"飞书连接测试成功，已创建一条测试记录：{record_id}")
    print("请在表格中确认图片可见；测试记录之后可手动删除。")


def select_latest_batch_names(batches_root: Path, count: int) -> list[str]:
    if count < 1:
        raise FeishuSyncError("--latest-batches 必须是大于 0 的整数。")
    if not batches_root.exists():
        return []
    directories = sorted(
        (path for path in batches_root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    return [path.name for path in directories[:count]]


def is_searchable_nickname(value: str) -> bool:
    return any(character.isalnum() for character in value)


def sync_feishu(
    config: dict[str, Any],
    latest_batches: int | None = None,
    dry_run: bool = False,
    batch_names: list[str] | None = None,
) -> None:
    client = create_feishu_client()
    candidates = collect_candidates(
        ROOT,
        project_path(config["paths"]["database"]),
    )
    selected_batches: list[str] = []
    if batch_names is not None:
        selected_batches = list(batch_names)
    elif latest_batches is not None:
        selected_batches = select_latest_batch_names(
            project_path(config["paths"]["output_batches"]),
            latest_batches,
        )
    if batch_names is not None or latest_batches is not None:
        allowed = set(selected_batches)
        candidates = [
            candidate for candidate in candidates if candidate.batch_name in allowed
        ]
        print("限定批次：" + ("、".join(selected_batches) or "无"))
    unsearchable = [
        candidate for candidate in candidates if not is_searchable_nickname(candidate.nickname)
    ]
    if unsearchable:
        print("以下昵称无法用于搜索，已排除并保留在本地等待人工修正：")
        for candidate in unsearchable:
            print(f"- {candidate.nickname} | {candidate.batch_name}")
        candidates = [
            candidate for candidate in candidates if is_searchable_nickname(candidate.nickname)
        ]
    if not candidates:
        print("没有可同步的正式成品。待复核图片不会自动上传。")
        return
    print(f"准备同步 {len(candidates)} 条正式成品：")
    for candidate in candidates:
        print(
            f"- {candidate.nickname} | {candidate.batch_name} | "
            f"查询码 {candidate.query_code}"
        )
    if dry_run:
        print("预览结束：未向飞书上传任何记录。")
        return
    state = SyncState(FEISHU_STATE_PATH)
    try:
        result = sync_candidates(client, candidates, state)
    finally:
        state.close()
    print(
        "同步完成："
        f"新增 {result['uploaded']}，跳过重复 {result['skipped']}，"
        f"失败 {result['failed']}。"
    )


def process_and_sync_feishu(
    config: dict[str, Any],
    assume_yes: bool = False,
) -> None:
    batches_root = project_path(config["paths"]["output_batches"])
    before = {
        path.name for path in batches_root.iterdir() if path.is_dir()
    } if batches_root.exists() else set()
    reports = process_inbox(config)
    if not reports:
        print("没有处理新的截图，因此没有上传飞书。")
        return
    after = {
        path.name for path in batches_root.iterdir() if path.is_dir()
    }
    new_batches = sorted(after - before)
    if not new_batches:
        raise FeishuSyncError("处理完成但未找到本次新批次，已停止上传。")

    print("\n本次处理完成，先预览即将上传的正式成品：")
    sync_feishu(config, dry_run=True, batch_names=new_batches)
    if not assume_yes:
        answer = input("\n输入 UPLOAD 确认上传本次批次，直接回车则取消：").strip()
        if answer != "UPLOAD":
            print("已取消上传；本地批次和成品均已保留。")
            return
    print("\n开始上传本次批次到飞书：")
    sync_feishu(config, batch_names=new_batches)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="猫咪抱新粉丝图片批量生成器")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-templates", help="为模板生成Alpha蒙版")
    prepare.add_argument("--name", help="只处理指定模板文件夹")
    setup = subparsers.add_parser("setup-template", help="兼容旧版：生成模板蒙版")
    setup.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    subparsers.add_parser("choose-template", help="交互选择当前模板")
    select = subparsers.add_parser("set-template", help="按名称切换模板")
    select.add_argument("name", help="templates 下的文件夹名称")
    subparsers.add_parser("list-templates", help="列出模板与当前选择")
    subparsers.add_parser("process", help="处理 inbox 中的现有截图")
    subparsers.add_parser("watch", help="持续监听 inbox 文件夹")
    subparsers.add_parser("status", help="查看处理状态")
    feishu_setup = subparsers.add_parser(
        "configure-feishu", help="配置飞书表格并检查字段"
    )
    feishu_setup.add_argument("--url", help="飞书多维表格完整网址")
    subparsers.add_parser("test-feishu", help="上传一条飞书连接测试记录")
    feishu_sync = subparsers.add_parser(
        "sync-feishu", help="同步正式成品到飞书多维表格"
    )
    feishu_sync.add_argument(
        "--latest-batches",
        type=int,
        metavar="N",
        help="只同步按文件夹名称排序最新的 N 个批次",
    )
    feishu_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览将要同步的记录，不上传",
    )
    process_sync = subparsers.add_parser(
        "process-and-sync", help="批量处理 inbox 并只上传本次新批次"
    )
    process_sync.add_argument(
        "--yes",
        action="store_true",
        help="跳过 UPLOAD 确认，仅用于明确授权的自动化运行",
    )
    return parser


def main() -> int:
    configure_console()
    config = load_config()
    ensure_directories(config)
    arguments = build_parser().parse_args()
    try:
        if arguments.command == "prepare-templates":
            prepare_templates(config, arguments.name)
        elif arguments.command == "setup-template":
            prepare_templates(config)
        elif arguments.command == "choose-template":
            choose_template(config)
        elif arguments.command == "set-template":
            set_active_template(config, arguments.name)
        elif arguments.command == "list-templates":
            list_templates(config)
        elif arguments.command == "process":
            process_inbox(config)
        elif arguments.command == "watch":
            watch(config)
        elif arguments.command == "status":
            show_status(config)
        elif arguments.command == "configure-feishu":
            configure_feishu(arguments.url)
        elif arguments.command == "test-feishu":
            test_feishu_connection()
        elif arguments.command == "sync-feishu":
            sync_feishu(config, arguments.latest_batches, arguments.dry_run)
        elif arguments.command == "process-and-sync":
            process_and_sync_feishu(config, arguments.yes)
    except FeishuSyncError as exc:
        print(f"飞书操作失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
