"""Per-project render lock backed by an operating system file lock.

The lock disappears by itself when the holding process ends, so a crash never leaves it stuck.
"""
from __future__ import annotations

import os

try:
    import msvcrt
except ImportError:          # not Windows
    msvcrt = None
    import fcntl


class LockError(RuntimeError):
    """Raised when another render already holds the project lock."""


class ProjectLock:
    def __init__(self, project):
        self.path = project.work_dir / ".lock"
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        try:
            if msvcrt is not None:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            raise LockError("another render is already running for this project") from e
        self.fd = fd
        return self

    def __exit__(self, *exc):
        fd, self.fd = self.fd, None
        if fd is None:
            return
        try:
            if msvcrt is not None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(fd)
