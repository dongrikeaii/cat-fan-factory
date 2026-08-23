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
    def test_process_and_sync_targets_only_the_new_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batches = root / "output" / "batches"
            (batches / "old-batch").mkdir(parents=True)
            config = {
                "paths": {
                    "output_batches": "output/batches",
                }
            }

            def fake_process(_config):
                (batches / "new-batch").mkdir()
                return [{"source": "one.jpg"}]

            with patch.object(app, "ROOT", root):
                with patch.object(app, "process_inbox", side_effect=fake_process):
                    with patch.object(app, "sync_feishu", return_value=1) as sync:
                        with redirect_stdout(io.StringIO()):
                            app.process_and_sync_feishu(config, assume_yes=True)
            self.assertEqual(2, sync.call_count)
            self.assertEqual(
                {"dry_run": True, "batch_names": ["new-batch"]},
                sync.call_args_list[0].kwargs,
            )
            self.assertEqual(
                {"batch_names": ["new-batch"]},
                sync.call_args_list[1].kwargs,
            )

    def test_process_and_sync_does_not_prompt_when_preview_is_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batches = root / "output" / "batches"
            batches.mkdir(parents=True)
            config = {"paths": {"output_batches": "output/batches"}}

            def fake_process(_config):
                (batches / "empty-batch").mkdir()
                return [{"source": "one.jpg"}]

            with patch.object(app, "ROOT", root):
                with patch.object(app, "process_inbox", side_effect=fake_process):
                    with patch.object(app, "sync_feishu", return_value=0) as sync:
                        with patch("builtins.input") as prompt:
                            with redirect_stdout(io.StringIO()):
                                app.process_and_sync_feishu(config)
            sync.assert_called_once_with(
                config, dry_run=True, batch_names=["empty-batch"]
            )
            prompt.assert_not_called()

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

    def test_template_base_jpg_is_auto_rotated_and_converted(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "templates" / "phone-photo"
            directory.mkdir(parents=True)
            photo = Image.new("RGB", (30, 20), "orange")
            exif = Image.Exif()
            exif[274] = 6
            photo.save(directory / "cat_base.jpg", exif=exif)
            Image.new("RGBA", (20, 30), (255, 255, 255, 255)).save(
                directory / "paw_foreground.png"
            )

            converted, message = app.ensure_template_base_png(directory)

            self.assertEqual(directory / "cat_base.png", converted)
            self.assertIn("cat_base.jpg", message)
            with Image.open(converted) as result:
                self.assertEqual((20, 30), result.size)

    def test_template_wizard_processes_selected_convertible_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "templates" / "new-bite"
            directory.mkdir(parents=True)
            Image.new("RGB", (20, 30), "gray").save(directory / "cat_base.jpg")
            Image.new("RGBA", (20, 30), (255, 255, 255, 127)).save(
                directory / "paw_foreground.png"
            )
            config = make_config()

            with patch.object(app, "ROOT", root):
                with patch("builtins.input", return_value="1"):
                    with redirect_stdout(io.StringIO()) as captured:
                        results = app.template_wizard(config)

            self.assertEqual("new-bite", results[0]["template"])
            self.assertTrue((directory / "cat_base.png").is_file())
            self.assertTrue((directory / "paw_mask_debug.png").is_file())
            self.assertIn("可自动转换", captured.getvalue())

    def test_top_anchor_keeps_different_card_heights_aligned(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "fixed-top"
            directory.mkdir()
            Image.new("RGB", (100, 200), "black").save(directory / "cat_base.png")
            Image.new("RGBA", (100, 200), (0, 0, 0, 0)).save(
                directory / "paw_foreground.png"
            )
            options = dict(TEMPLATE_DEFAULTS)
            options.update(
                {
                    "card_width_ratio": 0.5,
                    "angle_degrees": 0,
                    "top_y_ratio": 0.25,
                    "shadow_blur": 0,
                    "shadow_opacity": 0,
                }
            )
            template = app.TemplateBundle(
                name="fixed-top",
                directory=directory,
                base=directory / "cat_base.png",
                paw_overlay=directory / "paw_foreground.png",
                mask_debug=directory / "paw_mask_debug.png",
                options=options,
            )

            short = app.compose_card(Image.new("RGB", (100, 20), "red"), template)
            tall = app.compose_card(Image.new("RGB", (100, 80), "red"), template)

            self.assertEqual(50, short.getbbox()[1])
            self.assertEqual(50, tall.getbbox()[1])

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
