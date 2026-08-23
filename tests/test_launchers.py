import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_all_private_screenshots_use_the_unified_inbox(self):
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("inbox/*", rules)
        self.assertNotIn("comment_inbox", rules)

    def test_batch_files_use_windows_line_endings_and_absolute_paths(self):
        for name in (
            "00_安装环境.bat",
            "01_处理一次.bat",
            "02_持续监听.bat",
            "03_切换模板.bat",
            "04_生成模板蒙版.bat",
            "05_查看状态.bat",
            "06_配置飞书.bat",
            "07_同步飞书.bat",
            "08_批量处理并上传飞书.bat",
        ):
            content = (ROOT / name).read_bytes()
            self.assertIn(b"\r\n", content, name)
            self.assertNotIn(b"\n", content.replace(b"\r\n", b""), name)
            self.assertIn(b"%~dp0", content, name)
            self.assertIn(b"%PYTHON_EXE%", content, name)


if __name__ == "__main__":
    unittest.main()
