import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge._files import atomic_write_text


class AtomicWriteTextTests(unittest.TestCase):
    def test_parent_directory_is_not_created_by_shared_writer(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "record.txt"

            with self.assertRaises(FileNotFoundError):
                atomic_write_text(target, "内容\n")

            self.assertFalse(target.parent.exists())

    def test_writes_utf8_text_with_lf_and_replaces_target(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / "record.txt"
            target.write_bytes(b"old")

            atomic_write_text(target, "中文第一行\n中文第二行\n")

            content = target.read_bytes()
            self.assertEqual(content, "中文第一行\n中文第二行\n".encode())
            self.assertNotIn(b"\r\n", content)

    def test_failed_replace_removes_temporary_file_and_preserves_target(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / "record.txt"
            target.write_bytes(b"old")

            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    atomic_write_text(target, "new\n")

            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(tuple(target.parent.glob(f".{target.name}.*.tmp")), ())


if __name__ == "__main__":
    unittest.main()
