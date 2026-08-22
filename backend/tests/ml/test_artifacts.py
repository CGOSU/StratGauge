import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge.ml._artifacts import write_immutable_bundle


class ImmutableBundleTests(unittest.TestCase):
    def test_writes_text_and_bytes_without_leaving_temporary_files(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "bundle"
            write_immutable_bundle(
                destination,
                {"manifest.json": '{"ok": true}\n', "model.bin": b"\x00\x01"},
                already_exists_message="bundle already exists",
            )

            self.assertEqual((destination / "manifest.json").read_bytes(), b'{"ok": true}\n')
            self.assertEqual((destination / "model.bin").read_bytes(), b"\x00\x01")
            self.assertEqual(
                {path.name for path in destination.iterdir()},
                {"manifest.json", "model.bin"},
            )

    def test_refuses_existing_output_before_writing_other_files(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "bundle"
            destination.mkdir()
            existing = destination / "manifest.json"
            existing.write_bytes(b"old")

            with self.assertRaisesRegex(FileExistsError, "bundle already exists"):
                write_immutable_bundle(
                    destination,
                    {"manifest.json": b"new", "model.bin": b"model"},
                    already_exists_message="bundle already exists",
                )

            self.assertEqual(existing.read_bytes(), b"old")
            self.assertFalse((destination / "model.bin").exists())
            self.assertEqual(tuple(destination.glob("*.tmp")), ())

    def test_removes_partial_bundle_when_replacement_fails(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "bundle"
            original_replace = Path.replace
            replace_count = 0

            def fail_on_second_replace(path: Path, target: Path) -> Path:
                nonlocal replace_count
                replace_count += 1
                if replace_count == 2:
                    raise OSError("replace failed")
                return original_replace(path, target)

            with patch.object(Path, "replace", new=fail_on_second_replace):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    write_immutable_bundle(
                        destination,
                        {"first.json": b"first", "second.json": b"second"},
                        already_exists_message="bundle already exists",
                    )

            self.assertFalse((destination / "first.json").exists())
            self.assertFalse((destination / "second.json").exists())
            self.assertEqual(tuple(destination.glob("*.tmp")), ())

    def test_removes_temporary_files_when_writing_fails(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "bundle"
            original_write_bytes = Path.write_bytes
            write_count = 0

            def fail_on_second_write(path: Path, data: bytes) -> int:
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise OSError("write failed")
                return original_write_bytes(path, data)

            with patch.object(Path, "write_bytes", new=fail_on_second_write):
                with self.assertRaisesRegex(OSError, "write failed"):
                    write_immutable_bundle(
                        destination,
                        {"first.json": b"first", "second.json": b"second"},
                        already_exists_message="bundle already exists",
                    )

            self.assertFalse((destination / "first.json").exists())
            self.assertFalse((destination / "second.json").exists())
            self.assertEqual(tuple(destination.glob("*.tmp")), ())


if __name__ == "__main__":
    unittest.main()
