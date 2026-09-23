"""Crash-safe, database-path-scoped ownership for CalorieK service processes.

This is a cooperative application guard, not protection against arbitrary SQL
tools. A service process has one owner thread per database. Multiple adapters
on that thread share the lease; cross-thread access is rejected, not serialized
at the too-late individual-transaction boundary.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import threading
import weakref


class DatabaseInUseError(RuntimeError):
    """Another CalorieK writer owns this database."""


class _OSLock:
    def __init__(self, path: Path, key: str) -> None:
        self.handle = None
        self.file = None
        if os.name == "nt":
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
            kernel.ReleaseMutex.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle.restype = wintypes.BOOL
            self.kernel = kernel
            # Global namespace also excludes another Windows login session. No
            # marker file is needed, and an abandoned mutex is safely acquired.
            name = "Global\\CalorieK.Writer." + hashlib.sha256(key.encode("utf-8")).hexdigest()
            handle = kernel.CreateMutexW(None, False, name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 0x80):  # WAIT_OBJECT_0, WAIT_ABANDONED
                error = ctypes.get_last_error()
                kernel.CloseHandle(handle)
                if result == 0x102:  # WAIT_TIMEOUT
                    raise DatabaseInUseError(f"数据库已由另一个 CalorieK 进程使用：{path}")
                raise OSError(error, f"无法取得数据库进程锁：{path}")
            self.handle = handle
        else:
            import fcntl

            path.parent.mkdir(parents=True, exist_ok=True)
            # Never unlink this file on release: unlink/recreate can split locks.
            self.file = Path(str(path) + ".writer.lock").open("a+b")
            try:
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self.file.close()
                self.file = None
                raise DatabaseInUseError(f"数据库已由另一个 CalorieK 进程使用：{path}") from exc

    def close(self) -> None:
        if self.handle is not None:
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.file is not None:
            self.file.close()  # OS releases flock even after a crash
            self.file = None


class _Ownership:
    def __init__(self, path: Path, key: str) -> None:
        self.pid = os.getpid()
        self.thread = threading.current_thread()
        self.lock = _OSLock(path, key)
        self.references = 0

    def check(self) -> None:
        # Thread objects, unlike numeric identifiers, cannot be reused after an
        # owner thread exits and abandons its Windows mutex.
        if self.pid != os.getpid() or self.thread is not threading.current_thread():
            raise DatabaseInUseError(
                "同一数据库只能由所属进程的单一线程使用；请在该线程执行完整服务操作。"
            )


_registry: dict[str, _Ownership] = {}
_registry_lock = threading.RLock()


def _release(key: str, state: _Ownership) -> None:
    with _registry_lock:
        # A forked child must not unlock its parent's inherited OS ownership.
        if state.pid != os.getpid():
            return
        state.references -= 1
        if state.references == 0:
            # Windows mutex ownership is thread-affine. If the last Python
            # reference is collected elsewhere, retain the reservation until
            # the owner reacquires/releases it or the process exits. Never
            # silently close an owned mutex from the wrong thread.
            if os.name == "nt" and state.thread is not threading.current_thread():
                return
            state.lock.close()
            _registry.pop(key, None)


class WriterLease:
    """Reference-counted lease; acquire before any read/derive/write workflow."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path).expanduser().resolve()
        key = os.path.normcase(str(path))
        with _registry_lock:
            state = _registry.get(key)
            if state is None:
                state = _Ownership(path, key)
                _registry[key] = state
            state.check()
            state.references += 1
            self._state = state
            self._finalizer = weakref.finalize(self, _release, key, state)

    def check(self) -> None:
        if not self._finalizer.alive:
            raise RuntimeError("database writer lease is closed")
        self._state.check()

    def close(self) -> None:
        if self._finalizer.alive:
            self._state.check()
            self._finalizer()

    def __enter__(self) -> WriterLease:
        self.check()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
