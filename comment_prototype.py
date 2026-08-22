from __future__ import annotations

import argparse
import json
import re
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

import app


ROOT = Path(__file__).resolve().parent
COMMENT_INBOX = ROOT / "comment_inbox"
COMMENT_OUTPUT = ROOT / "output" / "comment_batches"


@dataclass(frozen=True)
class CommentRow:
    index: int
    top: int
    bottom: int
    nickname: str
    nickname_confidence: float
    review_reasons: list[str]

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def is_reply_anchor(item: app.OcrItem) -> bool:
    compact = compact_text(item.text)
    if item.confidence < 0.7 or compact == "未回复":
        return False
    if compact == "回复":
        return True
    time_prefix = re.search(
        r"(?:刚刚|\d+(?:秒钟|分钟|小时|天)前|昨天|前天|\d{1,2}:\d{2})",
        compact,
    )
    return bool(time_prefix and compact.endswith("回复"))


def first_complete_row_top(
    image: Image.Image,
    items: list[app.OcrItem],
    anchors: list[app.OcrItem],
    anchor_bottoms: list[int],
    margin: int,
) -> int | None:
    filter_items = [
        item
        for item in items
        if item.top < anchors[0].top
        and any(
            fragment in compact_text(item.text)
            for fragment in ("未回复", "粉丝", "最早发布", "最新发布")
        )
    ]
    if not filter_items:
        return None
    top = int(round(max(item.bottom for item in filter_items) + margin))
    first_height = anchor_bottoms[0] - top
    anchor_gaps = [
        current - previous
        for previous, current in zip(anchor_bottoms, anchor_bottoms[1:])
    ]
    minimum_height = image.height * 0.06
    if anchor_gaps:
        minimum_height = max(minimum_height, statistics.median(anchor_gaps) * 0.6)
    return top if first_height >= minimum_height else None


def is_nickname_candidate(
    item: app.OcrItem,
    width: int,
    top: int,
    bottom: int,
) -> bool:
    center_y = (item.top + item.bottom) / 2
    row_height = bottom - top
    compact = compact_text(item.text)
    if not compact or compact == "回复":
        return False
    if item.left < width * 0.1 or item.left > width * 0.68:
        return False
    if center_y < top or center_y > top + row_height * 0.42:
        return False
    if re.search(r"\d{1,2}:\d{2}", compact):
        return False
    return not any(
        fragment in compact
        for fragment in ("评论管理", "搜索评论", "未回复", "最早发布")
    )


def detect_comment_rows(
    image: Image.Image,
    items: list[app.OcrItem],
    minimum_name_confidence: float,
) -> list[CommentRow]:
    anchors = sorted(
        (item for item in items if is_reply_anchor(item)),
        key=lambda item: (item.top, item.left),
    )
    if not anchors:
        return []
    margin = max(12, int(round(image.height * 0.01)))
    anchor_bottoms = [
        min(image.height, int(round(anchor.bottom + margin))) for anchor in anchors
    ]
    bounds: list[tuple[int, int]] = []
    first_top = first_complete_row_top(
        image, items, anchors, anchor_bottoms, margin
    )
    if first_top is not None:
        bounds.append((first_top, anchor_bottoms[0]))
    bounds.extend(zip(anchor_bottoms, anchor_bottoms[1:]))
    rows: list[CommentRow] = []
    for index, (top, bottom) in enumerate(bounds, start=1):
        if bottom <= top:
            continue
        candidates = [
            item
            for item in items
            if is_nickname_candidate(item, image.width, top, bottom)
        ]
        candidates.sort(key=lambda item: (item.top, -item.confidence, item.left))
        nickname = candidates[0].text.strip() if candidates else "昵称待确认"
        confidence = candidates[0].confidence if candidates else 0.0
        reasons: list[str] = []
        if not candidates:
            reasons.append("昵称未识别，可能是纯表情昵称")
        elif confidence < minimum_name_confidence:
            reasons.append(f"昵称OCR置信度较低：{confidence:.3f}")
        rows.append(
            CommentRow(
                index=index,
                top=top,
                bottom=bottom,
                nickname=nickname,
                nickname_confidence=confidence,
                review_reasons=reasons,
            )
        )
    return rows


def timestamp_directory(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    candidate = root / stamp
    counter = 2
    while candidate.exists():
        candidate = root / f"{stamp}_{counter}"
        counter += 1
    candidate.mkdir()
    return candidate


def process_comment_images(
    paths: Iterable[Path],
    template_name: str | None = None,
) -> Path:
    config = app.load_config()
    template = app.resolve_template(config, template_name)
    app.ensure_template_ready(template)
    batch = timestamp_directory(COMMENT_OUTPUT)
    final = batch / "final"
    crops = batch / "cropped_rows"
    review = batch / "needs_review"
    for directory in (final, crops, review):
        directory.mkdir()
    ocr = app.OcrReader()
    reports: list[dict[str, Any]] = []
    for path in paths:
        image = Image.open(path).convert("RGB")
        items = ocr.read(image)
        rows = detect_comment_rows(
            image,
            items,
            float(config["ocr"]["minimum_name_confidence"]),
        )
        report: dict[str, Any] = {
            "source": path.name,
            "reply_anchors": sum(is_reply_anchor(item) for item in items),
            "complete_rows": len(rows),
            "items": [],
        }
        for row in rows:
            crop = image.crop((0, row.top, image.width, row.bottom))
            nickname_file = app.safe_filename(row.nickname, f"row_{row.index:02d}")
            stem = f"{path.stem}_{row.index:02d}_{nickname_file}"
            crop_path = crops / f"{stem}.png"
            crop.save(crop_path, optimize=True)
            result = app.compose_card(crop, template)
            output_root = review if row.needs_review else final
            output_path = output_root / f"{stem}.jpg"
            result.save(
                output_path,
                quality=int(config["output"]["jpeg_quality"]),
                subsampling=0,
                optimize=True,
            )
            report["items"].append(
                {
                    "row": row.index,
                    "nickname": row.nickname,
                    "ocr_confidence": round(row.nickname_confidence, 4),
                    "bounds": [row.top, row.bottom],
                    "review": row.needs_review,
                    "reasons": row.review_reasons,
                    "output": str(output_path.relative_to(ROOT)),
                }
            )
        reports.append(report)
    payload = {
        "batch": batch.name,
        "template": template.name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "comment_screenshot_prototype",
        "screenshots": reports,
    }
    (batch / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"评论测试批次：{batch}")
    return batch


def input_images(values: list[str]) -> list[Path]:
    if values:
        paths = [Path(value).expanduser().resolve() for value in values]
    else:
        COMMENT_INBOX.mkdir(exist_ok=True)
        paths = [
            path
            for path in sorted(COMMENT_INBOX.iterdir())
            if path.is_file() and path.suffix.lower() in app.IMAGE_SUFFIXES
        ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("找不到评论截图：" + "、".join(missing))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="评论截图切割与小猫成品测试")
    parser.add_argument("--input", action="append", default=[], help="评论截图路径")
    parser.add_argument("--template", help="模板名称；默认使用当前模板")
    arguments = parser.parse_args()
    paths = input_images(arguments.input)
    if not paths:
        print(f"没有评论截图。请放入：{COMMENT_INBOX}")
        return 0
    process_comment_images(paths, arguments.template)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
