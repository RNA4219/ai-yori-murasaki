"""一覧の再検証に使うファイル属性。未変更の台帳から本文を取得しない。"""
import os
from pathlib import Path
import stat


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _open = _kernel.CreateFileW
    _open.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    _open.restype = wintypes.HANDLE
    _info = _kernel.GetFileInformationByHandleEx
    _info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _kernel.CloseHandle.argtypes = (wintypes.HANDLE,)

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in ("created", "accessed", "written", "changed")] + [
            ("attributes", wintypes.DWORD)]

    def _change_time(path: Path) -> int:
        # Windowsのst_ctimeは作成日時。属性だけを開き、NTFSの変更日時を取得する。
        handle = _open(str(path), 0x80, 7, None, 3, 0x00200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = _FileBasicInfo()
            if not _info(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            return info.changed
        finally:
            _kernel.CloseHandle(handle)


def file_stamp(path: Path) -> tuple | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    linked = stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
    changed = info.st_ctime_ns
    if os.name == "nt" and stat.S_ISREG(info.st_mode) and not linked:
        changed = _change_time(path)
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, changed
