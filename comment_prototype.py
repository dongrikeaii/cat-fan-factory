from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

import app


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


def detect_comment_avatar_bounds(image: Image.Image) -> list[tuple[int, int]]:
    rgb = np.asarray(image.convert("RGB"))
    width, height = image.size
    band_width = max(1, int(round(width * 0.135)))
    avatar_band = rgb[:, :band_width]
    nonwhite = (255 - avatar_band.min(axis=2)) > 25
    row_counts = nonwhite.sum(axis=1)
    minimum_row_pixels = max(6, int(round(width * 0.01)))
    active = (row_counts >= minimum_row_pixels).astype(np.uint8).reshape(-1, 1)
    close_height = max(7, int(round(width * 0.018)))
    active = cv2.morphologyEx(
        active, cv2.MORPH_CLOSE, np.ones((close_height, 1), np.uint8)
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(active, 8)
    minimum_height = width * 0.045
    maximum_height = width * 0.14
    minimum_width = width * 0.05
    bounds: list[tuple[int, int]] = []
    for label in range(1, count):
        _, top, _, component_height, _ = stats[label]
        bottom = top + component_height
        if not minimum_height <= component_height <= maximum_height:
            continue
        segment = nonwhite[top:bottom]
        columns = np.where(segment.any(axis=0))[0]
        if not len(columns) or columns[-1] - columns[0] + 1 < minimum_width:
            continue
        bounds.append((int(top), int(bottom)))
    return sorted(bounds)


def is_comment_screenshot(
    items: list[app.OcrItem], image: Image.Image | None = None
) -> bool:
    texts = [compact_text(item.text) for item in items]
    anchors = [item for item in items if is_reply_anchor(item)]
    has_anchor = bool(anchors)
    has_comment_header = any("评论管理" in text for text in texts)
    has_filter_bar = any("未回复" in text for text in texts) and any(
        fragment in text
        for text in texts
        for fragment in ("粉丝", "最早发布", "最新发布")
    )
    if not has_anchor:
        return False
    if has_comment_header or has_filter_bar or len(anchors) >= 2:
        return True
    return image is not None and bool(detect_comment_avatar_bounds(image))


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
    avatar_bounds = detect_comment_avatar_bounds(image)
    if not anchors and not avatar_bounds:
        return []
    margin = max(12, int(round(image.height * 0.01)))
    bounds: list[tuple[int, int]] = []
    using_avatar_bounds = bool(avatar_bounds)
    if using_avatar_bounds:
        row_tops = [max(0, top - margin) for top, _ in avatar_bounds]
        for index, top in enumerate(row_tops):
            bottom = row_tops[index + 1] if index + 1 < len(row_tops) else image.height
            bounds.append((top, bottom))
    else:
        anchor_bottoms = [
            min(image.height, int(round(anchor.bottom + margin))) for anchor in anchors
        ]
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
        has_reply = any(
            top <= (anchor.top + anchor.bottom) / 2 <= bottom for anchor in anchors
        )
        if using_avatar_bounds and index == len(bounds) and not has_reply:
            reasons.append("截图底部未检测到“回复”，内容可能不完整")
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
