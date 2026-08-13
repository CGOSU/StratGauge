from __future__ import annotations

import io
import sys
import threading
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import start  # noqa: E402


class FakeProcess:
    def __init__(self, return_codes: list[int | None]) -> None:
        self._return_codes = iter(return_codes)
        self.returncode: int | None = None
        self.stdout = io.StringIO("")
        self.send_signal_called = False
        self.terminate_called = False
        self.kill_called = False
        self.wait_called = False
        self.pid = 1000

    def poll(self) -> int | None:
        try:
            self.returncode = next(self._return_codes)
        except StopIteration:
            pass
        return self.returncode

    def send_signal(self, signum: int) -> None:
        del signum
        self.send_signal_called = True
        self.returncode = 0

    def terminate(self) -> None:
        self.terminate_called = True
        self.returncode = 0

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_called = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeProcessTree:
    def __init__(self) -> None:
        self.added: list[FakeProcess] = []
        self.closed = False

    def add(self, process: FakeProcess) -> None:
        self.added.append(process)

    def terminate(self) -> None:
        for process in self.added:
            process.terminate()

    def close(self) -> None:
        self.closed = True


class StartLauncherTests(unittest.TestCase):
    def test_service_specs_use_fixed_working_directories_and_commands(self) -> None:
        backend, frontend = start.build_service_specs(
            ROOT,
            uv_executable="uv.exe",
            corepack_executable="corepack.cmd",
        )

        self.assertEqual(backend.name, "backend")
        self.assertEqual(backend.working_directory, ROOT / "backend")
        self.assertEqual(
            backend.command,
            (
                "uv.exe",
                "run",
                "uvicorn",
                "stratgauge.api.app:app",
                "--reload",
                "--app-dir",
                "src",
            ),
        )
        self.assertEqual(frontend.name, "frontend")
        self.assertEqual(frontend.working_directory, ROOT / "frontend")
        self.assertEqual(frontend.command, ("corepack.cmd", "pnpm", "dev"))

    def test_output_is_prefixed_by_service_name(self) -> None:
        output = io.StringIO()
        with patch.object(sys, "stdout", output):
            start._write_service_output(
                "backend",
                io.StringIO("line one\nline two"),
                threading.Lock(),
            )

        self.assertEqual(output.getvalue(), "[backend] line one\n[backend] line two\n")

    def test_second_service_start_failure_stops_first_service(self) -> None:
        first = FakeProcess([None])
        tree = FakeProcessTree()
        specs = start.build_service_specs(ROOT, uv_executable="uv", corepack_executable="corepack")

        with patch.object(start, "_start_process", side_effect=[first, OSError("missing")]):
            runner = start.ServiceRunner(specs, cast(start.ProcessTree, tree))
            result = runner.run()

        self.assertEqual(result, 2)
        self.assertTrue(first.terminate_called)
        self.assertTrue(first.wait_called)
        self.assertTrue(tree.closed)
        self.assertEqual(tree.added, [first])

    def test_nonzero_service_exit_is_propagated_and_other_service_is_stopped(self) -> None:
        failed = FakeProcess([None, 7])
        other = FakeProcess([None])
        tree = FakeProcessTree()
        specs = start.build_service_specs(ROOT)

        with patch.object(start, "_start_process", side_effect=[failed, other]):
            runner = start.ServiceRunner(specs, cast(start.ProcessTree, tree))
            result = runner.run()

        self.assertEqual(result, 7)
        self.assertTrue(other.terminate_called)
        self.assertTrue(other.wait_called)
        self.assertTrue(tree.closed)

    def test_signal_request_returns_interrupt_code_and_stops_services(self) -> None:
        first = FakeProcess([None])
        second = FakeProcess([None])
        tree = FakeProcessTree()
        specs = start.build_service_specs(ROOT)

        with patch.object(start, "_start_process", side_effect=[first, second]):
            runner = start.ServiceRunner(specs, cast(start.ProcessTree, tree))
            with patch.object(runner._stop_event, "wait", side_effect=[True]):
                result = runner.run()

        self.assertEqual(result, 130)
        self.assertTrue(first.terminate_called)
        self.assertTrue(second.terminate_called)
        self.assertTrue(tree.closed)


if __name__ == "__main__":
    unittest.main()
