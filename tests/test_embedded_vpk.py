"""Contract checks using tiny package engines; no GPU or model data required."""
import hashlib
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from zephie_rolling_on.decision_models.discover import discover_decision_models
from zephie_rolling_on.decision_models.embedded_vpk import EmbeddedVpkModel
from zephie_rolling_on.decision_models.runtime import clear_active_decision_model, set_active_decision_model
from zephie_rolling_on.decision_models.vela_package import VelaPackageModel

SOURCE = '''
from dataclasses import dataclass
@dataclass
class Engine:
    context: object
    closed: bool = False
    def load(self):
        assert self.context.read_resource('data.bin') == b'example'
        self.context.backend_changed({'device':'cpu','label':'CPU'})
    def decide(self, **kwargs):
        return dict(action='roll', rl_action=1, native_code=0, v_cells=42.)
    def close(self): self.closed = True
def create_engine(context): return Engine(context)
'''


def package(path, source=SOURCE, **overrides):
    resources={'engine.py':source.encode(), 'data.bin':b'example'}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        for name,data in resources.items():z.writestr(name,data)
    payload=buffer.getvalue()
    header=dict(format='vela-package',format_version=1,model_id=path.stem,name=path.stem,
                engine_type='python-v1',engine_api=1,engine_entry='engine.py',
                busy_png_bytes=0,done_png_bytes=0,payload_bytes=len(payload),
                payload_sha256=hashlib.sha256(payload).hexdigest(),
                resources_sha256={k:hashlib.sha256(v).hexdigest() for k,v in resources.items()})
    header.update(overrides)
    raw=json.dumps(header).encode()
    path.write_bytes(struct.pack('<I',len(raw))+raw+payload)
    return EmbeddedVpkModel(path)


class EmbeddedContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)

    def tearDown(self):
        clear_active_decision_model()
        self.temp.cleanup()

    def test_discovery_does_not_execute_and_future_model_works(self):
        p=self.root/'future.vpk'
        package(p,source="raise RuntimeError('do not execute during discovery')")
        found=discover_decision_models(self.root)
        self.assertEqual(len(found),1)
        self.assertFalse(found[0].package.is_imported)
        self.assertFalse(found[0].package.import_model().ok)
        m=package(p,source=SOURCE.replace('42.','99.'))
        self.assertTrue(m.import_model().ok)
        self.assertEqual(m.decide(cell_id=1,dice_remaining=1).v_cells,99.)
        m.close()

    def test_corruption_and_unknown_api_rejected(self):
        for changes in ({'engine_api':2},{'payload_sha256':'0'*64},
                        {'resources_sha256':{'engine.py':'0'*64}}):
            m=package(self.root/'bad.vpk',**changes)
            self.assertFalse(m.import_model().ok)
            self.assertFalse(m.is_imported)

    def test_legacy_discovery_stays_native(self):
        p=self.root/'old.vpk'
        package(p,engine_type=None)
        model=discover_decision_models(self.root)[0].package
        self.assertIs(type(model),VelaPackageModel)

    def test_switch_releases_engine_and_module(self):
        m=package(self.root/'one.vpk')
        self.assertTrue(m.import_model().ok)
        module=m._engine_module.__name__
        engine=m._engine
        self.assertIn(module,sys.modules)
        set_active_decision_model(m)
        set_active_decision_model(m)
        self.assertFalse(engine.closed)
        clear_active_decision_model()
        self.assertTrue(engine.closed)
        self.assertNotIn(module,sys.modules)
        self.assertTrue(m.import_model().ok)
        m.close()

    def test_input_and_output_validation(self):
        m=package(self.root/'one.vpk')
        with self.assertRaises(RuntimeError):m.decide(cell_id=1,dice_remaining=1)
        self.assertTrue(m.import_model().ok)
        for args in (dict(hand=['unknown']),dict(drawn={'forward_1':-1}),dict(cell_id=-1)):
            inputs=dict(cell_id=1,dice_remaining=1);inputs.update(args)
            with self.assertRaises(ValueError):m.decide(**inputs)
        with self.assertRaises(RuntimeError):m.decide(cell_id=1,dice_remaining=0)
        m._engine.decide=lambda **kw:dict(action='forward_1',rl_action=3,native_code=1,v_cells=1.)
        with self.assertRaises(RuntimeError):m.decide(cell_id=1,dice_remaining=1,hand=['forward_1'])
        m.close()


if __name__=='__main__':unittest.main()
