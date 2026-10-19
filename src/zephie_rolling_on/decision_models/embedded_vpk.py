"""Versioned VPKs with a readable Python engine and model resources.

Engine code executes only after explicit import_model(), never during discovery.
Packages execute trusted local code; their hashes detect damage, not authorship.
"""
from __future__ import annotations

import hashlib
import io
import math
import sys
import threading
import types
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .types import DecisionModelDisplayInfo, DecisionModelImportResult, DecisionResult, NATIVE_CARD_IDS
from .vela_package import VelaPackageModel, VelaPackageError, _VOCAB_INDEX

ENGINE_TYPE = 'python-v1'
MAX_CONTENT_BYTES = 512 << 20


class EngineContext:
    """Engine API v1: immutable resource bytes, settings and status callbacks."""
    api_version = 1

    def __init__(self, model_id, resources, settings, progress, backend_changed):
        self.model_id = model_id
        self._resources = resources
        self.settings = dict(settings)
        self.progress = progress
        self.backend_changed = backend_changed

    def read_resource(self, name: str) -> bytes:
        return self._resources[name]


class EmbeddedVpkModel(VelaPackageModel):
    def __init__(self, package_path: Path, *, settings: dict | None = None):
        super().__init__(package_path)
        self._engine = None
        self._context = None
        self._engine_module = None
        self._settings = dict(settings or {})
        self._lock = threading.RLock()
        self.runtime_info: dict = {}

    @property
    def runtime_device_label(self) -> str:
        return str(self.runtime_info.get('label', ''))

    def _backend_changed(self, info: dict):
        self.runtime_info = dict(info)
        self._info = None

    def display_info(self):
        info = super().display_info()
        return DecisionModelDisplayInfo(
            model_id=info.model_id, name=info.name, version=info.version, summary=info.summary,
            param_lines=info.param_lines,
            status_line=info.status_line + (' · ' + self.runtime_device_label if self.runtime_device_label else ''))

    def _resources(self):
        h = self._header
        if h.get('engine_type') != ENGINE_TYPE or h.get('engine_api') != 1 or h.get('format_version') != 1:
            raise VelaPackageError('模型包引擎接口不兼容，请更新软件')
        sizes = [h.get(k, 0) for k in ('busy_png_bytes', 'done_png_bytes', 'payload_bytes')]
        if any(type(n) is not int or n < 0 for n in sizes) or not 0 < sizes[2] <= MAX_CONTENT_BYTES:
            raise VelaPackageError('模型包长度无效')
        offset = self._after_header + sizes[0] + sizes[1]
        if self.package_path.stat().st_size != offset + sizes[2]:
            raise VelaPackageError('模型包长度不符，请重新下载')
        with self.package_path.open('rb') as f:
            f.seek(offset)
            payload = f.read(sizes[2])
        if hashlib.sha256(payload).hexdigest() != h.get('payload_sha256'):
            raise VelaPackageError('模型包校验失败，请重新下载')
        expected = h.get('resources_sha256')
        if not isinstance(expected, dict) or h.get('engine_entry') not in expected:
            raise VelaPackageError('缺少模型包资源清单')
        resources = {}
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            names = z.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise VelaPackageError('模型包资源清单不符')
            if sum(i.file_size for i in z.infolist()) > MAX_CONTENT_BYTES:
                raise VelaPackageError('模型包解压尺寸过大')
            for item in z.infolist():
                path = PurePosixPath(item.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in item.filename or item.is_dir():
                    raise VelaPackageError('模型包资源名称无效')
                data = z.read(item)
                if hashlib.sha256(data).hexdigest() != expected[item.filename]:
                    raise VelaPackageError('模型包资源校验失败')
                resources[item.filename] = data
        return resources

    def import_model(self, *, on_progress: Callable[[int, str], None] | None = None):
        with self._lock:
            if self._imported:
                return DecisionModelImportResult(True, '模型已导入（' + self.runtime_device_label + '）')
            report = on_progress or (lambda percent, message: None)
            try:
                report(0, '读取模型包')
                resources = self._resources()
                entry = self._header['engine_entry']
                context = EngineContext(self.model_id, resources, self._settings, report, self._backend_changed)
                module = types.ModuleType('_vpk_' + uuid.uuid4().hex)
                module.__file__ = str(self.package_path) + '/' + entry
                self._engine_module = module
                sys.modules[module.__name__] = module
                exec(compile(resources[entry].decode('utf-8'), module.__file__, 'exec'), module.__dict__)
                factory = getattr(module, 'create_engine', None)
                if not callable(factory):
                    raise VelaPackageError('模型包缺少 create_engine(context)')
                self._context = context
                self._engine = factory(context)
                if self._engine.load() is False:
                    raise VelaPackageError('模型引擎加载失败')
                self._imported = True
                report(100, '模型就绪：' + self.runtime_device_label)
                return DecisionModelImportResult(True, '模型导入完成（' + self.runtime_device_label + '）')
            except Exception as exc:
                self.close()
                return DecisionModelImportResult(False, f'模型导入失败：{exc}')

    def decide(self, *, cell_id, dice_remaining, next_roll_free=False, hand=(), drawn=None):
        with self._lock:
            if not self._imported or self._engine is None:
                raise RuntimeError('model not imported; call import_model first')
            h = list(hand)
            d = dict(drawn) if drawn is not None else {c: h.count(c) for c in h}
            if len(h) > 5 or any(c not in NATIVE_CARD_IDS for c in h):
                raise ValueError('Invalid hand')
            if any(c not in NATIVE_CARD_IDS or type(n) is not int or n < 0 for c, n in d.items()):
                raise ValueError('Invalid drawn counts')
            if type(cell_id) is not int or cell_id < 0 or type(dice_remaining) is not int or dice_remaining < 0:
                raise ValueError('Invalid position or dice count')
            result = self._engine.decide(cell_id=cell_id, dice_remaining=dice_remaining,
                                         next_roll_free=bool(next_roll_free), hand=h, drawn=d)
            out = result if isinstance(result, DecisionResult) else DecisionResult(**result)
            if not math.isfinite(out.v_cells) or not 0 <= out.rl_action <= 23 or not 0 <= out.native_code <= len(h):
                raise RuntimeError('模型返回了无效决策')
            if out.action == 'roll':
                valid = out.rl_action == 1 and out.native_code == 0 and (dice_remaining > 0 or next_roll_free)
            elif out.action == 'error':
                valid = out.rl_action == 0 and out.native_code == 0
            else:
                valid = out.native_code > 0 and h[out.native_code-1] == out.action and out.rl_action == 2 + _VOCAB_INDEX.get(out.action, -100)
            if not valid:
                raise RuntimeError('模型返回动作与手牌槽位不一致')
            return out

    def close(self):
        with self._lock:
            engine, self._engine = self._engine, None
            self._imported = False
            self._context = None
            if engine is not None and callable(getattr(engine, 'close', None)):
                try:
                    engine.close()
                except Exception:
                    pass
            if self._engine_module is not None:
                sys.modules.pop(self._engine_module.__name__, None)
                self._engine_module = None
            self.runtime_info = {}
            self._info = None
