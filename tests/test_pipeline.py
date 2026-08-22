import json
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (
    OcrItem,
    analyze_row,
    detect_rows,
    hash_distance,
    image_dhash,
    normalize_name,
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

    def test_dhash_is_stable_for_small_brightness_change(self):
        gradient = np.tile(np.arange(96, dtype=np.uint8), (96, 1))
        first = Image.fromarray(gradient, mode="L")
        second = Image.fromarray(np.clip(gradient + 3, 0, 255), mode="L")
        self.assertLessEqual(hash_distance(image_dhash(first), image_dhash(second)), 2)

    def test_normalize_name_ignores_case_width_and_spaces(self):
        self.assertEqual(normalize_name(" Sixteen Ghost "), "sixteenghost")
        self.assertEqual(normalize_name("Ｓｔｅｌｌａ"), "stella")


if __name__ == "__main__":
    unittest.main()
