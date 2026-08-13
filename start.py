"""Start the StratGauge backend and frontend development servers together."""

from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, Protocol, TextIO, cast

BACKEND_COMMAND = (
    "run",
    "uvicorn",
    "stratgauge.api.app:app",
    "--reload",
    "--app-dir",
    "src",
)
FRONTEND_COMMAND = ("pnpm", "dev")
SHUTDOWN_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.1


class StartupError(RuntimeError):
    """Raised when the unified development environment cannot be started."""


@dataclass(frozen=True)
class ServiceSpec:
    """The immutable command and working directory for one development service."""

    name: str
    working_directory: Path
    command: tuple[str, ...]


@dataclass
class RunningService:
    """A service process and its output reader."""

    spec: ServiceSpec
    process: subprocess.Popen[str]
    output_thread: threading.Thread


class ProcessTree(Protocol):
    """Track child processes so they can be terminated as one tree."""

    def add(self, process: subprocess.Popen[str]) -> None:
        """Add a process to the tracked process tree."""

    def terminate(self) -> None:
        """Terminate any remaining processes in the tracked tree."""

    def close(self) -> None:
        """Release the tree and apply its platform-specific cleanup policy."""


class NoopProcessTree:
    """Process-tree implementation used on POSIX, where process groups are used."""

    def add(self, process: subprocess.Popen[str]) -> None:
        del process

    def terminate(self) -> None:
        pass

    def close(self) -> None:
        pass


if os.name == "nt":

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


class WindowsJobObject:
    """Kill all tracked descendants when the launcher exits on Windows."""

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsJobObject is only available on Windows")

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._root_process_ids: list[int] = []
        self._configure_api()
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise _windows_error("CreateJobObjectW")

        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = self._kernel32.SetInformationJobObject(
            self._handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not configured:
            self.close()
            raise _windows_error("SetInformationJobObject")

    def _configure_api(self) -> None:
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._kernel32.SetInformationJobObject.restype = ctypes.c_int
        self._kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._kernel32.TerminateJobObject.restype = ctypes.c_int
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int

    def add(self, process: subprocess.Popen[str]) -> None:
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise StartupError("无法取得 Windows 子进程句柄，不能启用进程树清理。")
        assigned = self._kernel32.AssignProcessToJobObject(self._handle, process_handle)
        if not assigned:
            raise _windows_error("AssignProcessToJobObject")
        self._root_process_ids.append(process.pid)

    def terminate(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self._kernel32.TerminateJobObject(handle, 1)
        for process_id in self._root_process_ids:
            _taskkill_process_tree(process_id)

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self._kernel32.CloseHandle(handle)
            self._handle = None


def _windows_error(operation: str) -> OSError:
    error_code = ctypes.get_last_error()
    return OSError(error_code, f"{operation} failed with Windows error {error_code}")


def create_process_tree() -> ProcessTree:
    """Create the platform-specific process-tree controller."""

    if os.name == "nt":
        return WindowsJobObject()
    return NoopProcessTree()


def repository_root() -> Path:
    """Return the repository root independently of the caller's current directory."""

    return Path(__file__).resolve().parent


def build_service_specs(
    root: Path,
    *,
    uv_executable: str = "uv",
    corepack_executable: str = "corepack",
) -> tuple[ServiceSpec, ServiceSpec]:
    """Build the fixed backend/frontend commands used by the launcher."""

    backend = (root / "backend").resolve()
    frontend = (root / "frontend").resolve()
    return (
        ServiceSpec("backend", backend, (uv_executable, *BACKEND_COMMAND)),
        ServiceSpec("frontend", frontend, (corepack_executable, *FRONTEND_COMMAND)),
    )


def _taskkill_process_tree(process_id: int) -> None:
    taskkill = shutil.which("taskkill") or shutil.which("taskkill.exe")
    if taskkill is None:
        return
    subprocess.run(
        [taskkill, "/PID", str(process_id), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )


def _find_executable(candidates: tuple[str, ...], label: str) -> str:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    names = "、".join(candidates)
    raise StartupError(f"找不到 {label}（{names}）。请先安装并确保它在 PATH 中。")


def resolve_tools() -> tuple[str, str]:
    """Resolve uv and Corepack without invoking a shell or installing anything."""

    uv = _find_executable(("uv", "uv.exe"), "uv")
    if os.name == "nt":
        corepack = _find_executable(("corepack.cmd", "corepack.exe", "corepack"), "Corepack")
    else:
        corepack = _find_executable(("corepack",), "Corepack")
    return uv, corepack


def validate_repository(root: Path) -> None:
    """Validate files needed for a local development startup."""

    backend = root / "backend"
    frontend = root / "frontend"
    required_paths = (backend / "pyproject.toml", frontend / "package.json")
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise StartupError(f"项目目录不完整，缺少：{', '.join(missing)}")
    if not (frontend / "node_modules").is_dir():
        raise StartupError(
            "前端依赖尚未安装。请先在 frontend 目录执行 "
            "`corepack pnpm install --frozen-lockfile`。启动器不会自动安装依赖。"
        )


def _start_process(spec: ServiceSpec) -> subprocess.Popen[str]:
    arguments = list(spec.command)
    common_arguments: dict[str, Any] = {
        "cwd": str(spec.working_directory),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "shell": False,
    }
    if os.name == "nt":
        common_arguments["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        common_arguments["start_new_session"] = True
    return cast(subprocess.Popen[str], subprocess.Popen(arguments, **common_arguments))


def _write_console(text: str) -> None:
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        sys.stdout.write(safe_text)


def _write_service_output(name: str, stream: TextIO, output_lock: threading.Lock) -> None:
    try:
        for line in iter(stream.readline, ""):
            with output_lock:
                _write_console(f"[{name}] {line}")
                if not line.endswith("\n"):
                    _write_console("\n")
                sys.stdout.flush()
    finally:
        stream.close()


class ServiceRunner:
    """Start, monitor and stop the two development services."""

    def __init__(self, specs: tuple[ServiceSpec, ...], process_tree: ProcessTree) -> None:
        self._specs = specs
        self._process_tree = process_tree
        self._running: list[RunningService] = []
        self._output_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stop_code = 130
        self._installed_signals: dict[int, Any] = {}

    def run(self) -> int:
        self._install_signal_handlers()
        try:
            self._start_services()
            return self._monitor_services()
        except StartupError as error:
            _write_error(str(error))
            return 2
        except KeyboardInterrupt:
            return 130
        finally:
            self._stop_services()
            self._process_tree.terminate()
            self._restore_signal_handlers()
            self._process_tree.close()

    def _install_signal_handlers(self) -> None:
        signals = [signal.SIGINT]
        for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
            candidate = getattr(signal, name, None)
            if candidate is not None and candidate not in signals:
                signals.append(candidate)
        for signum in signals:
            previous = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)
            self._installed_signals[signum] = previous

    def _restore_signal_handlers(self) -> None:
        for signum, previous in self._installed_signals.items():
            signal.signal(signum, previous)
        self._installed_signals.clear()

    def _handle_signal(self, signum: int, _frame: FrameType | None) -> None:
        self._stop_code = 128 + signum
        self._stop_event.set()

    def _start_services(self) -> None:
        for spec in self._specs:
            if not spec.working_directory.is_dir():
                raise StartupError(f"{spec.name} 工作目录不存在：{spec.working_directory}")
            try:
                process = _start_process(spec)
            except OSError as error:
                raise StartupError(
                    f"启动 {spec.name} 失败：{error}. 命令：{' '.join(spec.command)}"
                ) from error

            try:
                self._process_tree.add(process)
            except (OSError, StartupError) as error:
                _terminate_process(process)
                raise StartupError(f"无法纳入 {spec.name} 的进程树清理：{error}") from error

            stream = process.stdout
            if stream is None:
                _terminate_process(process)
                raise StartupError(f"无法读取 {spec.name} 的启动日志。")
            output_thread = threading.Thread(
                target=_write_service_output,
                args=(spec.name, stream, self._output_lock),
                name=f"{spec.name}-output",
                daemon=True,
            )
            output_thread.start()
            self._running.append(RunningService(spec, process, output_thread))

            early_exit = process.poll()
            if early_exit is not None:
                raise StartupError(
                    f"{spec.name} 启动后立即退出，退出码：{_display_return_code(early_exit)}。"
                )

    def _monitor_services(self) -> int:
        while not self._stop_event.wait(POLL_INTERVAL_SECONDS):
            for service in self._running:
                return_code = service.process.poll()
                if return_code is not None:
                    if return_code != 0:
                        _write_error(
                            f"{service.spec.name} 已退出，退出码："
                            f"{_display_return_code(return_code)}。"
                        )
                    return _normalized_return_code(return_code)
        return self._stop_code

    def _stop_services(self) -> None:
        for service in reversed(self._running):
            _stop_process(service)
        for service in self._running:
            service.output_thread.join(timeout=1.0)
        self._running.clear()


def _write_error(message: str) -> None:
    print(f"[launcher] {message}", file=sys.stderr, flush=True)


def _display_return_code(return_code: int) -> int:
    return return_code if return_code >= 0 else 128 + (-return_code)


def _normalized_return_code(return_code: int) -> int:
    return _display_return_code(return_code)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _send_to_process_group(process: subprocess.Popen[str], signal_name: str) -> bool:
    kill_group = getattr(os, "killpg", None)
    get_process_group = getattr(os, "getpgid", None)
    signal_value = getattr(signal, signal_name, None)
    if not callable(kill_group) or not callable(get_process_group) or signal_value is None:
        return False
    try:
        kill_group(get_process_group(process.pid), signal_value)
    except (OSError, ValueError):
        return False
    return True


def _send_graceful_stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.terminate()
        return
    if _send_to_process_group(process, "SIGINT"):
        return
    process.terminate()


def _force_stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        _taskkill_process_tree(process.pid)
    else:
        _send_to_process_group(process, "SIGKILL")
    try:
        process.kill()
    except OSError:
        pass


def _stop_process(service: RunningService) -> None:
    process = service.process
    if process.poll() is not None:
        return
    _send_graceful_stop(process)
    try:
        process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _force_stop(process)
        try:
            process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def main() -> int:
    root = repository_root()
    try:
        validate_repository(root)
        uv, corepack = resolve_tools()
        specs = build_service_specs(root, uv_executable=uv, corepack_executable=corepack)
        process_tree = create_process_tree()
    except (OSError, StartupError) as error:
        _write_error(str(error))
        return 2

    print("[launcher] 启动 backend : http://127.0.0.1:8000", flush=True)
    print("[launcher] 启动 frontend: http://127.0.0.1:5173", flush=True)
    return ServiceRunner(specs, process_tree).run()


if __name__ == "__main__":
    raise SystemExit(main())
