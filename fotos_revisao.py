"""Fotos locais pertencem à revisão, sem sobrescrever uma oferta em envio."""
import os
import re
import tempfile
from pathlib import Path


async def save_revision_photo(message, chat_id, revision, media_dir, download, rebrand=None):
    if not re.fullmatch(r'[0-9a-f]{64}', str(revision)):
        raise ValueError('Digest nativo da revisão inválido')
    media_dir = Path(media_dir)
    final = media_dir / f'{int(chat_id)}_{int(message.id)}_{revision}.jpg'
    if final.is_file() and 0 < final.stat().st_size <= 10_000_000:
        return final
    fd, name = tempfile.mkstemp(prefix='.capture-', suffix='.jpg', dir=media_dir)
    os.close(fd)
    staging = Path(name)
    try:
        downloaded = await download(message, file=str(staging))
        if not downloaded or not staging.is_file() or not (0 < staging.stat().st_size <= 10_000_000):
            return None
        if rebrand is not None:
            await rebrand(staging)
        if not staging.is_file() or not (0 < staging.stat().st_size <= 10_000_000):
            return None
        try:
            # Publicação atômica, sem substituir um arquivo que já está em uso.
            os.link(staging, final)
        except FileExistsError:
            if not final.is_file() or not (0 < final.stat().st_size <= 10_000_000):
                raise ValueError('Foto da revisão persistida está inválida')
        return final
    finally:
        staging.unlink(missing_ok=True)
