import sys
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import OcrItem
from comment_prototype import detect_comment_rows, is_reply_anchor


class CommentPrototypeTests(unittest.TestCase):
    def test_exact_reply_text_is_anchor_but_filter_is_not(self):
        self.assertTrue(is_reply_anchor(OcrItem("回复", 0.99, 300, 100, 360, 130)))
        self.assertTrue(
            is_reply_anchor(
                OcrItem("2分钟前 · 云南 回复", 0.99, 120, 100, 380, 130)
            )
        )
        self.assertFalse(
            is_reply_anchor(OcrItem("未回复", 0.99, 50, 20, 150, 50))
        )
        self.assertFalse(
            is_reply_anchor(OcrItem("请回复", 0.99, 120, 220, 250, 255))
        )

    def test_complete_first_row_after_filters_is_included(self):
        image = Image.new("RGB", (828, 900), "white")
        items = [
            OcrItem("未回复", 0.99, 50, 100, 150, 130),
            OcrItem("最新发布", 0.99, 610, 100, 760, 130),
            OcrItem("暖暖橙橙", 0.98, 120, 175, 260, 205),
            OcrItem("礼貌投稿", 0.99, 120, 220, 300, 255),
            OcrItem("2分钟前 · 云南 回复", 0.99, 120, 280, 380, 310),
            OcrItem("我又干嘛了", 0.98, 120, 370, 260, 405),
            OcrItem("我也想要", 0.99, 120, 420, 300, 455),
            OcrItem("2分钟前 · 辽宁 回复", 0.99, 120, 480, 380, 510),
        ]
        rows = detect_comment_rows(image, items, 0.72)
        self.assertEqual(["暖暖橙橙", "我又干嘛了"], [row.nickname for row in rows])

    def test_adjacent_reply_anchors_support_variable_row_heights(self):
        image = Image.new("RGB", (828, 900), "white")
        items = [
            OcrItem("未回复", 0.99, 50, 20, 150, 50),
            OcrItem("回复", 0.99, 340, 100, 400, 130),
            OcrItem("Happy", 0.98, 120, 170, 210, 205),
            OcrItem("我也要！", 0.99, 120, 220, 250, 255),
            OcrItem("回复", 0.99, 340, 300, 400, 330),
            OcrItem("Yutoo", 0.97, 120, 370, 210, 405),
            OcrItem("第一行评论", 0.99, 120, 420, 300, 455),
            OcrItem("第二行评论", 0.99, 120, 465, 300, 500),
            OcrItem("回复", 0.99, 340, 600, 400, 630),
        ]
        rows = detect_comment_rows(image, items, 0.72)
        self.assertEqual(["Happy", "Yutoo"], [row.nickname for row in rows])
        self.assertEqual(200, rows[0].bottom - rows[0].top)
        self.assertEqual(300, rows[1].bottom - rows[1].top)

    def test_missing_emoji_nickname_goes_to_review(self):
        image = Image.new("RGB", (828, 600), "white")
        items = [
            OcrItem("回复", 0.99, 340, 100, 400, 130),
            OcrItem("我也要", 0.99, 120, 230, 220, 265),
            OcrItem("回复", 0.99, 340, 300, 400, 330),
        ]
        rows = detect_comment_rows(image, items, 0.72)
        self.assertEqual("昵称待确认", rows[0].nickname)
        self.assertTrue(rows[0].needs_review)


if __name__ == "__main__":
    unittest.main()
