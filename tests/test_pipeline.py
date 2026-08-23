import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
from app import (
    BatchPaths,
    Database,
    OcrItem,
    RowAnalysis,
    TemplateBundle,
    analyze_row,
    detect_rows,
    hash_distance,
    image_dhash,
    normalize_name,
    process_screenshot,
)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (Path(__file__).resolve().parents[1] / "config.json").open(
            "r", encoding="utf-8"
        ) as handle:
            cls.config = json.load(handle)

    def test_detects_ten_regular_rows(self):
        image = Image.new("RGB", (828, 1792), "white")
        draw = ImageDraw.Draw(image)
        for center_y in [271 + 168 * index for index in range(10)]:
            draw.rounded_rectangle(
                (568, center_y - 29, 728, center_y + 29),
                radius=12,
                fill=(255, 43, 85),
            )
        rows = detect_rows(image, self.config)
        self.assertEqual(10, len(rows))
        self.assertTrue(rows[-1].partial)

    def test_header_ellipsis_does_not_hide_red_button_rows(self):
        image = Image.new("RGB", (828, 1792), "white")
        draw = ImageDraw.Draw(image)
        for center_x in (757, 773, 789):
            draw.ellipse(
                (center_x - 3, 136, center_x + 3, 142),
                fill="black",
            )
        for center_y in [268 + 168 * index for index in range(10)]:
            draw.rounded_rectangle(
                (568, center_y - 28, 728, center_y + 28),
                radius=12,
                fill=(255, 43, 85),
            )

        rows = detect_rows(image, self.config)

        self.assertEqual(10, len(rows))
        self.assertEqual(268, round(rows[0].center))

    def test_ellipsis_markers_locate_rows_without_red_buttons(self):
        image = Image.new("RGB", (828, 1792), "white")
        draw = ImageDraw.Draw(image)
        for center_y in [295 + 160 * index for index in range(10)]:
            for center_x in (764, 776, 787):
                draw.ellipse(
                    (center_x - 2, center_y - 2, center_x + 2, center_y + 2),
                    fill="black",
                )
        rows = detect_rows(image, self.config)
        self.assertEqual(10, len(rows))
        self.assertEqual(295, round(rows[0].center))
        self.assertEqual(1735, round(rows[-1].center))

    def test_follower_list_uses_top_line_as_nickname(self):
        row = Image.new("RGB", (828, 160), "white")
        items = [
            OcrItem("Mik.", 0.99, 168, 40, 215, 67),
            OcrItem("回关", 1.0, 626, 68, 680, 96),
            OcrItem("After all, tomorrow is...", 0.96, 165, 84, 480, 114),
        ]
        analysis = analyze_row(row, items, False, self.config)
        self.assertEqual("Mik.", analysis.nickname)
        self.assertTrue(analysis.follow_marker_found)
        self.assertFalse(analysis.needs_review)

    def test_single_mutual_follow_row_is_supported(self):
        row = Image.new("RGB", (1178, 240), "white")
        detected = detect_rows(row, self.config)
        self.assertEqual([(0, 240)], [(item.top, item.bottom) for item in detected])
        items = [
            OcrItem("弹橘他", 0.99, 250, 45, 390, 105),
            OcrItem("幸福就是规律过一天一天", 0.98, 250, 125, 700, 175),
            OcrItem("互相关注", 0.99, 825, 80, 1015, 145),
        ]
        analysis = analyze_row(row, items, False, self.config)
        self.assertEqual("弹橘他", analysis.nickname)
        self.assertTrue(analysis.follow_marker_found)
        self.assertFalse(analysis.needs_review)

    def test_dedup_is_template_scoped_and_review_does_not_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = root / "output" / "batches" / "old" / "final"
            batch.mkdir(parents=True)
            (batch.parent / "report.json").write_text(
                json.dumps({"template": "classic-cat"}), encoding="utf-8"
            )
            database_path = root / "data" / "processed.sqlite3"
            database_path.parent.mkdir(parents=True)
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                CREATE TABLE processed_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nickname TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    avatar_hash TEXT NOT NULL,
                    name_hash TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    ocr_confidence REAL NOT NULL,
                    review_required INTEGER NOT NULL,
                    output_file TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO processed_entries VALUES (
                    NULL, '弹橘他', '弹橘他', '0000000000000000',
                    '1111111111111111', 'old.png', 1, 0.99, 0,
                    'output/batches/old/final/fan.jpg', '2026-08-22T12:00:00'
                )
                """
            )
            connection.commit()
            connection.close()

            analysis = RowAnalysis(
                nickname="弹橘他",
                normalized_name="弹橘他",
                nickname_confidence=0.99,
                follow_marker_found=True,
                ocr_text=["弹橘他", "互相关注"],
                avatar_hash="0000000000000000",
                name_hash="1111111111111111",
                needs_review=False,
                review_reasons=[],
            )
            database = Database(database_path, root)
            try:
                self.assertIsNotNone(database.find_duplicate(analysis, "classic-cat"))
                self.assertIsNone(database.find_duplicate(analysis, "Orange Cat"))
                review = RowAnalysis(
                    **{
                        **analysis.__dict__,
                        "needs_review": True,
                        "review_reasons": ["待复核"],
                    }
                )
                database.insert(
                    review,
                    "review.png",
                    1,
                    "output/batches/new/needs_review/fan.jpg",
                    "Orange Cat",
                )
                self.assertIsNone(database.find_duplicate(analysis, "Orange Cat"))
            finally:
                database.connection.close()

    def test_comment_dedup_uses_content_and_stays_separate_from_followers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = Database(root / "data" / "processed.sqlite3", root)
            first = RowAnalysis(
                nickname="Stella",
                normalized_name="stella",
                nickname_confidence=0.99,
                follow_marker_found=True,
                ocr_text=["第一条评论"],
                avatar_hash="0" * 16,
                name_hash="1" * 16,
                needs_review=False,
                review_reasons=[],
                content_key="first-comment",
            )
            second = RowAnalysis(
                **{
                    **first.__dict__,
                    "ocr_text": ["第二条评论"],
                    "content_key": "second-comment",
                }
            )
            try:
                database.insert(
                    first,
                    "comments.png",
                    1,
                    "output/batches/one/final/first.jpg",
                    "Orange Cat",
                    "comment",
                )
                self.assertIsNotNone(
                    database.find_duplicate(first, "Orange Cat", "comment")
                )
                self.assertIsNone(
                    database.find_duplicate(second, "Orange Cat", "comment")
                )
                self.assertIsNone(
                    database.find_duplicate(first, "Bite-cat", "comment")
                )
                self.assertIsNone(
                    database.find_duplicate(first, "Orange Cat", "follower")
                )
            finally:
                database.connection.close()

    def test_dhash_is_stable_for_small_brightness_change(self):
        gradient = np.tile(np.arange(96, dtype=np.uint8), (96, 1))
        first = Image.fromarray(gradient, mode="L")
        second = Image.fromarray(np.clip(gradient + 3, 0, 255), mode="L")
        self.assertLessEqual(hash_distance(image_dhash(first), image_dhash(second)), 2)

    def test_normalize_name_ignores_case_width_and_spaces(self):
        self.assertEqual(normalize_name(" Sixteen Ghost "), "sixteenghost")
        self.assertEqual(normalize_name("Ｓｔｅｌｌａ"), "stella")

    def test_main_pipeline_auto_detects_and_processes_comments(self):
        full_items = [
            OcrItem("评论管理", 0.99, 340, 20, 480, 60),
            OcrItem("未回复", 0.99, 50, 100, 150, 130),
            OcrItem("粉丝", 0.99, 360, 100, 450, 130),
            OcrItem("最新发布", 0.99, 610, 100, 760, 130),
            OcrItem("暖暖橙橙", 0.98, 120, 175, 260, 205),
            OcrItem("礼貌投稿", 0.99, 120, 220, 300, 255),
            OcrItem("2分钟前 · 云南 回复", 0.99, 120, 280, 380, 310),
            OcrItem("我又干嘛了", 0.98, 120, 370, 260, 405),
            OcrItem("我也想要", 0.99, 120, 420, 300, 455),
            OcrItem("2分钟前 · 辽宁 回复", 0.99, 120, 480, 380, 510),
        ]
        row_items = [
            [
                OcrItem("暖暖橙橙", 0.98, 120, 20, 260, 50),
                OcrItem("礼貌投稿", 0.99, 120, 70, 300, 105),
                OcrItem("2分钟前 · 云南 回复", 0.99, 120, 130, 380, 160),
            ],
            [
                OcrItem("我又干嘛了", 0.98, 120, 20, 260, 50),
                OcrItem("我也想要", 0.99, 120, 70, 300, 105),
                OcrItem("2分钟前 · 辽宁 回复", 0.99, 120, 130, 380, 160),
            ],
        ]

        class FakeOcr:
            def __init__(self):
                self.calls = 0

            def read(self, _image):
                self.calls += 1
                return full_items if self.calls == 1 else row_items[self.calls - 2]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "inbox" / "comments.png"
            source.parent.mkdir()
            Image.new("RGB", (828, 900), "white").save(source)
            batch_root = root / "output" / "batches" / "batch"
            batch = BatchPaths(
                root=batch_root,
                final=batch_root / "final",
                crops=batch_root / "cropped_rows",
                review=batch_root / "needs_review",
                sources=batch_root / "source_screenshots",
            )
            for directory in (
                batch.final,
                batch.crops,
                batch.review,
                batch.sources,
            ):
                directory.mkdir(parents=True)
            template_dir = root / "templates" / "Orange Cat"
            template_dir.mkdir(parents=True)
            template = TemplateBundle(
                "Orange Cat",
                template_dir,
                template_dir / "cat_base.png",
                template_dir / "paw_foreground.png",
                template_dir / "paw_mask_debug.png",
                {},
            )
            database = Database(root / "data" / "processed.sqlite3", root)
            try:
                with patch.object(app, "ROOT", root):
                    with patch.object(
                        app,
                        "compose_card",
                        return_value=Image.new("RGB", (20, 30), "white"),
                    ):
                        report = process_screenshot(
                            source,
                            self.config,
                            database,
                            FakeOcr(),
                            template,
                            batch,
                        )
                self.assertEqual("comment", report["entry_type"])
                self.assertEqual(2, report["generated"])
                self.assertEqual(
                    ["暖暖橙橙", "我又干嘛了"],
                    [item["nickname"] for item in report["items"]],
                )
                entry_types = database.connection.execute(
                    "SELECT DISTINCT entry_type FROM processed_entries"
                ).fetchall()
                self.assertEqual(["comment"], [row[0] for row in entry_types])
            finally:
                database.connection.close()


if __name__ == "__main__":
    unittest.main()
