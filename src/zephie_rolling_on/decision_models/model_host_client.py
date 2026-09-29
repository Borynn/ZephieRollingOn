"""Client for ``model_host.exe`` — the process that serves some model packages.

The application never reads a package itself: it sends a request and receives
the three return values. `model_host.exe` ships once with the application and
serves every package it is given.

Wire format:

    request : u32 cmd | u32 payload_len | payload
    response: i32 status | u32 payload_len | payload
    status == 1 is an import progress frame (payload = u32 percent)
"""

from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from zephie_rolling_on.decision_models.types import (
    CARD_TO_NATIVE,
    MAX_HAND,
    N_CARDS,
    DecisionModelDisplayInfo,
    DecisionModelImportResult,
    DecisionResult,
)
from zephie_rolling_on.paths import project_root

PACKAGE_GLOB = "zephie_*.zm"
PACKAGE_SUFFIX = ".zm"

_CMD_GET_INFO = 1
_CMD_IMPORT = 2
_CMD_DECIDE = 3
_CMD_GET_ART = 4
_CMD_PING = 5
_CMD_SHUTDOWN = 6

_STATUS_PROGRESS = 1

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.argtypes = [
    ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
]
_k32.CreateFileW.restype = ctypes.c_void_p
_k32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
_k32.ReadFile.restype = ctypes.c_int
_k32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
_k32.WriteFile.restype = ctypes.c_int
_k32.CloseHandle.argtypes = [ctypes.c_void_p]
_k32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
_k32.WaitNamedPipeW.restype = ctypes.c_int

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class ModelHostError(RuntimeError):
    """Transport or host-side failure."""


def model_id_from_filename(path: Path) -> str:
    """``zephie_<model_id>.zm`` -> ``<model_id>``."""
    stem = path.stem
    if stem.startswith("zephie_"):
        stem = stem[len("zephie_") :]
    return stem


def host_executable() -> Path | None:
    """Locate ``model_host.exe`` (portable layout or a development build)."""
    override = os.environ.get("ZEPHIE_MODEL_HOST")
    if override:
        p = Path(override)
        return p if p.is_file() else None

    root = project_root()
    candidates = [
        root / "model_host.exe",  # portable: beside the exe
        root / "bin" / "model_host.exe",
        root / "dist" / "model_host.exe",
    ]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).resolve().parent / "model_host.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


class _Pipe:
    """Blocking named-pipe endpoint."""

    def __init__(self, name: str, timeout_s: float = 30.0) -> None:
        self._h = None
        full = f"\\\\.\\pipe\\{name}"
        deadline = time.monotonic() + timeout_s
        last_err = 0
        while time.monotonic() < deadline:
            h = _k32.CreateFileW(full, _GENERIC_READ | _GENERIC_WRITE, 0, None,
                                 _OPEN_EXISTING, 0, None)
            if h and h != _INVALID_HANDLE:
                self._h = h
                return
            last_err = ctypes.get_last_error()
            _k32.WaitNamedPipeW(full, 200)
        raise ModelHostError(f"cannot connect to model host (winerr={last_err})")

    def close(self) -> None:
        if self._h:
            _k32.CloseHandle(self._h)
            self._h = None

    @property
    def closed(self) -> bool:
        return not self._h

    def _write(self, data: bytes) -> None:
        view = memoryview(data)
        off = 0
        while off < len(view):
            chunk = bytes(view[off : off + (1 << 20)])
            written = ctypes.c_uint32(0)
            ok = _k32.WriteFile(self._h, chunk, len(chunk), ctypes.byref(written), None)
            if not ok or written.value == 0:
                raise ModelHostError("pipe write failed")
            off += written.value

    def _read(self, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            want = min(n - len(out), 1 << 20)
            buf = ctypes.create_string_buffer(want)
            got = ctypes.c_uint32(0)
            ok = _k32.ReadFile(self._h, buf, want, ctypes.byref(got), None)
            if not ok or got.value == 0:
                raise ModelHostError("pipe read failed (host disconnected?)")
            out.extend(buf.raw[: got.value])
        return bytes(out)

    def request(self, cmd: int, payload: bytes = b"") -> None:
        self._write(struct.pack("<II", cmd, len(payload)) + payload)

    def response(self) -> tuple[int, bytes]:
        status, length = struct.unpack("<iI", self._read(8))
        return status, (self._read(length) if length else b"")


class _DmInfo(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char * 64),
        ("infer_time", ctypes.c_char * 128),
        ("performance", ctypes.c_char * 128),
    ]


class _DmDecideOut(ctypes.Structure):
    _fields_ = [
        ("rl_action", ctypes.c_int),
        ("native_code", ctypes.c_int),
        ("v_cells", ctypes.c_float),
        ("action_name", ctypes.c_char * 32),
    ]


class ModelHostProcess:
    """Owner of the host child process for this application instance."""

    _lock = threading.Lock()
    _proc: subprocess.Popen | None = None
    _pipe: _Pipe | None = None
    _models_dir: Path | None = None

    @classmethod
    def _ensure_started(cls, models_dir: Path) -> _Pipe:
        with cls._lock:
            if cls._pipe is not None and not cls._pipe.closed:
                return cls._pipe
            exe = host_executable()
            if exe is None:
                raise ModelHostError("model_host.exe not found next to the application")
            name = f"zephie_mh_{os.getpid()}_{int(time.time() * 1000) % 100000}"
            cls._proc = subprocess.Popen(
                [str(exe), "--pipe", name, "--model-dir", str(models_dir)],
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cls._pipe = _Pipe(name)
            cls._models_dir = models_dir
            return cls._pipe

    @classmethod
    def pipe(cls, models_dir: Path) -> _Pipe:
        if cls._pipe is not None and not cls._pipe.closed:
            return cls._pipe
        return cls._ensure_started(models_dir)

    @classmethod
    def shutdown(cls) -> None:
        with cls._lock:
            if cls._pipe is not None and not cls._pipe.closed:
                try:
                    cls._pipe.request(_CMD_SHUTDOWN)
                    cls._pipe.response()
                except Exception:  # noqa: BLE001
                    pass
                cls._pipe.close()
            cls._pipe = None
            if cls._proc is not None:
                try:
                    cls._proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    cls._proc.kill()
                cls._proc = None


class HostedDecisionModel:
    """One ``.zm`` package, driven through the host's three-call interface."""

    def __init__(self, package_path: Path, models_dir: Path | None = None) -> None:
        self.package_path = Path(package_path).resolve()
        self.model_id = model_id_from_filename(self.package_path)
        self.models_dir = Path(models_dir) if models_dir else self.package_path.parent
        self._info: DecisionModelDisplayInfo | None = None
        self._imported = False
        self._lock = threading.Lock()

    def _call(self, cmd: int, payload: bytes) -> tuple[int, bytes]:
        pipe = ModelHostProcess.pipe(self.models_dir)
        with self._lock:
            pipe.request(cmd, payload)
            return pipe.response()

    def _id_payload(self) -> bytes:
        raw = self.model_id.encode("utf-8")
        return struct.pack("<I", len(raw)) + raw

    def display_info(self) -> DecisionModelDisplayInfo:
        if self._info is not None:
            return self._info
        status, body = self._call(_CMD_GET_INFO, self._id_payload())
        if status != 0 or len(body) < ctypes.sizeof(_DmInfo):
            raise ModelHostError(f"get_info failed (status={status})")
        info = _DmInfo.from_buffer_copy(body)
        name = info.name.decode("utf-8", "replace")
        infer = info.infer_time.decode("utf-8", "replace")
        perf = info.performance.decode("utf-8", "replace")
        lines: list[str] = []
        if infer:
            lines.append(f"推理参考时间 {infer}")
        if perf:
            lines.append(f"模型表现 {perf}")
        self._info = DecisionModelDisplayInfo(
            model_id=self.model_id,
            name=name,
            param_lines=tuple(lines),
            status_line=self.package_path.name,
        )
        return self._info

    def import_model(
        self,
        *,
        on_progress: Callable[[int, str], None] | None = None,
    ) -> DecisionModelImportResult:
        path_raw = str(self.package_path).encode("utf-8")
        payload = self._id_payload() + struct.pack("<I", len(path_raw)) + path_raw
        pipe = ModelHostProcess.pipe(self.models_dir)
        with self._lock:
            pipe.request(_CMD_IMPORT, payload)
            while True:
                status, body = pipe.response()
                if status == _STATUS_PROGRESS:
                    pct = struct.unpack("<I", body)[0] if len(body) >= 4 else 0
                    if on_progress is not None:
                        on_progress(int(pct), "")
                    continue
                break

        if status != 0:
            self._imported = False
            return DecisionModelImportResult(ok=False, message=f"导入失败 (code {status})")
        self._imported = True
        return DecisionModelImportResult(ok=True, message="模型导入完成")

    @property
    def is_imported(self) -> bool:
        return self._imported

    def decide(
        self,
        *,
        cell_id: int,
        dice_remaining: int,
        next_roll_free: bool = False,
        hand: Sequence[str] = (),
        drawn: Mapping[str, int] | None = None,
    ) -> DecisionResult:
        if not self._imported:
            raise RuntimeError("model not imported; call import_model first")
        hand_list = [str(c) for c in hand]
        if len(hand_list) > MAX_HAND:
            raise ValueError(f"hand length {len(hand_list)} > {MAX_HAND}")

        hand_native = [0] * MAX_HAND
        for i, cid in enumerate(hand_list):
            if cid not in CARD_TO_NATIVE:
                raise ValueError(f"unknown card_id {cid!r}")
            hand_native[i] = CARD_TO_NATIVE[cid]

        counts = [0] * N_CARDS
        if drawn is None:
            for cid in hand_list:
                counts[CARD_TO_NATIVE[cid]] += 1
        else:
            for k, v in drawn.items():
                if k not in CARD_TO_NATIVE:
                    raise ValueError(f"unknown drawn card_id {k!r}")
                counts[CARD_TO_NATIVE[k]] = int(v)

        state = struct.pack(
            "<iiii" + "i" * MAX_HAND + "i" * N_CARDS,
            int(cell_id), int(dice_remaining), 1 if next_roll_free else 0,
            len(hand_list), *hand_native, *counts,
        )
        status, body = self._call(_CMD_DECIDE, self._id_payload() + state)
        if status != 0:
            raise RuntimeError(f"decide failed (code {status})")
        out = _DmDecideOut.from_buffer_copy(body)
        return DecisionResult(
            action=out.action_name.decode("utf-8", "replace"),
            rl_action=int(out.rl_action),
            native_code=int(out.native_code),
            v_cells=float(out.v_cells),
        )

    def import_art_png(self, which: str) -> bytes | None:
        """Import sticker PNG (``busy`` / ``done``) carried by the package."""
        kind = 0 if which.strip().lower() == "busy" else 1
        status, body = self._call(_CMD_GET_ART, self._id_payload() + struct.pack("<I", kind))
        if status != 0 or len(body) < 4:
            return None
        (n,) = struct.unpack("<I", body[:4])
        return body[4 : 4 + n] if n else None


__all__ = [
    "HostedDecisionModel",
    "ModelHostError",
    "ModelHostProcess",
    "host_executable",
    "model_id_from_filename",
]
