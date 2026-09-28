"""Restaura a arte original versionada em partes, sem rede nem recompressão."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile


def restore_banner(base, name="banner_cupons"):
    if name not in {"banner_cupons", "banner_cupons_ml"}:
        raise ValueError("Arte não reconhecida.")
    base = Path(base)
    parts = base / ('assets/' + name + '.parts')
    manifest = json.loads((parts / 'manifest.json').read_text(encoding='utf-8'))
    data = b''.join(base64.b64decode((parts / f'{i:03d}.b64').read_bytes(), validate=True)
                    for i in range(manifest['parts']))
    if len(data) != manifest['size'] or hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise ValueError('Banner incompleto ou corrompido; restaure os arquivos do repositório.')
    target = base / ('assets/' + name + '.png')
    if target.is_file() and target.read_bytes() == data:
        return target
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


if __name__ == '__main__':
    print(restore_banner(Path(__file__).resolve().parent))
