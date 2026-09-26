"""Trava local liberada pelo sistema operacional ao encerrar o processo."""
import os
from contextlib import contextmanager


@contextmanager
def instancia_unica(path):
    stream = open(path, 'a+b')
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit('Outro processo já usa esta função nesta pasta. Encerre a janela anterior.') from None
        yield
    finally:
        stream.close()
