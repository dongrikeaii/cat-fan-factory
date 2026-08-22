import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import app


TEMPLATE_DEFAULTS = {
    "card_width_ratio": 1.0,
    "angle_degrees": 20,
    "center_x_ratio": 0.5,
    "center_y_ratio": 0.7,
    "shadow_blur": 8,
    "shadow_opacity": 0.3,
    "shadow_offset_x": 4,
    "shadow_offset_y": 6,
}


def make_config() -> dict:
    return {
        "paths": {
            "templates": "templates",
            "active_template": "data/active_template.txt",
            "output_batches": "output/batches",
        },
        "default_template": "one",
        "template_defaults": TEMPLATE_DEFAULTS,
    }


def make_template(root: Path, name: str, alpha: int) -> None:
    directory = root / "templates" / name
    directory.mkdir(parents=True)
    Image.new("RGB", (20, 30), "gray").save(directory / "cat_base.png")
    Image.new("RGBA", (20, 30), (255, 255, 255, alpha)).save(
        directory / "paw_foreground.png"
    )


class TemplateAndBatchTests(unittest.TestCase):
    def test_searchable_nickname_requires_a_letter_or_number(self):
        self.assertFalse(app.is_searchable_nickname("..."))
        self.assertFalse(app.is_searchable_nickname("✨"))
        self.assertTrue(app.is_searchable_nickname("輒..."))
        self.assertTrue(app.is_searchable_nickname("HELLO77"))

    def test_select_latest_batch_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batches = root / "output" / "batches"
            for name in (
                "2026-08-21_21-56-09",
                "2026-08-21_22-10-03",
                "2026-08-21_22-08-02",
            ):
                (batches / name).mkdir(parents=True)
            self.assertEqual(
                ["2026-08-21_22-10-03", "2026-08-21_22-08-02"],
                app.select_latest_batch_names(batches, 2),
            )

    def test_mask_is_exact_alpha_and_template_can_switch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_template(root, "one", 127)
            make_template(root, "two", 255)
            config = make_config()
            with patch.object(app, "ROOT", root):
                with redirect_stdout(io.StringIO()):
                    result = app.prepare_templates(config)
                self.assertEqual(["one", "two"], [item["template"] for item in result])
                mask = Image.open(root / "templates" / "one" / "paw_mask_debug.png")
                self.assertEqual((127, 127), mask.getextrema())
                with redirect_stdout(io.StringIO()):
                    app.set_active_template(config, "two")
                self.assertEqual("two", app.active_template_name(config))

    def test_each_processing_run_gets_a_unique_timestamp_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = make_config()
            with patch.object(app, "ROOT", root):
                first = app.create_batch_paths(config)
                second = app.create_batch_paths(config)
                self.assertNotEqual(first.root, second.root)
                self.assertRegex(first.root.name, r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
                self.assertTrue(first.final.is_dir())
                self.assertTrue(first.crops.is_dir())
                self.assertTrue(first.review.is_dir())
                self.assertTrue(first.sources.is_dir())


if __name__ == "__main__":
    unittest.main()
