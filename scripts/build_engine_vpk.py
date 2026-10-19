"""Build an API v1 embedded-engine VPK from an engine and resource directory."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path


def build(engine: Path, resources: Path, output: Path, model_id: str, name: str):
    files = {p.relative_to(resources).as_posix(): p.read_bytes()
             for p in sorted(resources.rglob('*')) if p.is_file()}
    if 'engine.py' in files:
        raise ValueError('Resource directory must not contain engine.py')
    files['engine.py'] = engine.read_bytes()
    if sum(map(len, files.values())) > 512 << 20:
        raise ValueError('Resources exceed API v1 limit')
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_STORED) as archive:
        for key, data in sorted(files.items()):
            archive.writestr(zipfile.ZipInfo(key, (2026, 1, 1, 0, 0, 0)), data)
    data = payload.getvalue()
    sha = lambda value: hashlib.sha256(value).hexdigest()
    header = dict(format='vela-package', format_version=1, model_id=model_id, name=name,
                  engine_type='python-v1', engine_api=1, engine_entry='engine.py',
                  busy_png_bytes=0, done_png_bytes=0, payload_bytes=len(data),
                  payload_sha256=sha(data), resources_sha256={k: sha(v) for k, v in files.items()})
    raw = json.dumps(header, ensure_ascii=False).encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(struct.pack('<I', len(raw)) + raw + data)
    return sha(output.read_bytes())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Output already exists; choose a new file')
    print(build(args.engine, args.resources, args.out, args.model_id, args.name))
