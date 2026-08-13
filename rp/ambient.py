"""AMBIENT probes — host state, attached to no subject.

The environment everything else sits in. Not a property of any project, so it
belongs to a subject of its own: attaching "the system drive is full" to
eighteen repositories reports one fact eighteen times.

The governing rule for this kind: **it is a level, not an event.** Ambient
values are true *now* and meaningless as history unless deliberately sampled,
so they must never be accumulated. A disk that was full an hour ago and is fine
now reads as fine, and that is correct.

Prefers syscalls over subprocesses. `GlobalMemoryStatusEx` costs microseconds;
shelling out to query the same number costs tens of milliseconds and a process.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import urllib.error
import urllib.request
from functools import lru_cache

from .probes import run, text

WINDOWS = os.name == "nt"


# --------------------------------------------------------------- memory, time


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def memory_used_pct() -> float:
    """Physical memory in use. Zero when it cannot be read, which is honest —
    a machine whose memory is unreadable is not a machine under pressure."""
    if not WINDOWS:
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                info = {k.strip(): v for k, v in
                        (line.split(":", 1) for line in fh if ":" in line)}
            total = float(info["MemTotal"].split()[0])
            avail = float(info["MemAvailable"].split()[0])
            return (1 - avail / total) * 100.0
        except Exception:
            return 0.0
    try:
        status = _MemStatus()
        status.dwLength = ctypes.sizeof(_MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return float(status.dwMemoryLoad)
    except Exception:
        return 0.0


def commit_used_pct() -> float:
    """Page file / commit charge. The number that actually predicts an
    allocation failure — a machine can have free RAM and no commit left."""
    if not WINDOWS:
        return 0.0
    try:
        status = _MemStatus()
        status.dwLength = ctypes.sizeof(_MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        if not status.ullTotalPageFile:
            return 0.0
        used = status.ullTotalPageFile - status.ullAvailPageFile
        return used / status.ullTotalPageFile * 100.0
    except Exception:
        return 0.0


def uptime_days() -> float:
    if not WINDOWS:
        try:
            with open("/proc/uptime", encoding="utf-8") as fh:
                return float(fh.read().split()[0]) / 86400.0
        except Exception:
            return 0.0
    try:
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0 / 86400.0
    except Exception:
        return 0.0


# --------------------------------------------------------------- os condition


@lru_cache(maxsize=1)
def pending_reboot() -> bool:
    """A restart the machine is waiting for.

    Worth surfacing because it is invisible by design and explains a whole
    class of "why did that not take effect".
    """
    if not WINDOWS:
        return os.path.exists("/var/run/reboot-required")
    import winreg
    keys = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"),
    ]
    for hive, path in keys:
        try:
            with winreg.OpenKey(hive, path):
                return True
        except OSError:
            continue
    return False


# Services that register as Automatic and then stop themselves. Reporting them
# is not wrong so much as useless: it is their normal state, and eight of them
# bury the one service that is actually down. Matched by shape rather than by a
# list of specific names, so it keeps working as software comes and goes.
_SELF_STOPPING = ("update", "updater", "elevation", "crashhandler", "telemetry",
                  "reporting", "brave", "edgeupdate", "gupdate", "medic",
                  "sysmain", "wbiosrvc", "dosvc", "maps", "cdpusersvc")


@lru_cache(maxsize=1)
def failed_services() -> tuple[str, ...]:
    """Services set to start automatically that are not running.

    Only automatic ones: a manual service being stopped is its normal state.
    And not the ones that stop themselves by design — updaters and crash
    handlers register Automatic and exit, so counting them made eight
    "failures" out of which none was actionable.
    """
    if not WINDOWS:
        out = text(run(("systemctl", "--failed", "--no-legend", "--plain")))
        names = [line.split()[0] for line in out.splitlines() if line.strip()]
    else:
        out = text(run(("powershell", "-NoProfile", "-Command",
                        "Get-Service | Where-Object { $_.StartType -eq 'Automatic' "
                        "-and $_.Status -ne 'Running' } | Select-Object -Expand Name")))
        names = [line.strip() for line in out.splitlines() if line.strip()]
    return tuple(n for n in names
                 if not any(hint in n.lower() for hint in _SELF_STOPPING))


@lru_cache(maxsize=1)
def scheduled_task_failures() -> int:
    """Scheduled tasks whose last run did not succeed.

    A task that has been failing quietly for weeks is the archetype of work
    everyone believes is happening.
    """
    if not WINDOWS:
        return 0
    out = text(run(("schtasks", "/query", "/fo", "csv", "/v")))
    failures = 0
    for line in out.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 7:
            continue
        # Skip Microsoft's own maintenance tasks: they fail by design in ways
        # nobody acts on, and they would drown anything of ours.
        name = parts[1] if len(parts) > 1 else ""
        if name.startswith("\\Microsoft\\"):
            continue
        result = next((p for p in parts if p.lstrip("-").isdigit()
                       and p not in ("0",) and len(p) > 1), None)
        if result and result not in ("0", "267009", "267011"):
            failures += 1
    return failures


# --------------------------------------------------------------- what is up


@lru_cache(maxsize=1)
def resident_models() -> tuple[tuple[str, float], ...]:
    """Models an inference server currently holds in memory.

    A local endpoint, so this is ambient rather than remote: it describes this
    machine's state, not somebody else's service. Silent when nothing answers,
    which is the same as nothing being loaded for every purpose here.
    """
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=2) as r:
            data = json.load(r)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return ()
    out = []
    for m in data.get("models", []):
        size = float(m.get("size_vram") or m.get("size") or 0) / 1024**3
        out.append((str(m.get("name", "?")), size))
    return tuple(out)


# Measuring a 289 GB cache means walking a 289 GB tree, which took twenty-one
# seconds and would have run every ten minutes forever. Cache sizes move on the
# scale of days, so the answer is kept on disk with a long TTL — the probe stays
# ambient (a level, read cheaply) instead of becoming a scan.
_SIZE_TTL = 6 * 3600.0
_SIZE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "cachesizes.json")


def _load_sizes() -> dict:
    try:
        with open(_SIZE_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _dir_size_gb(path: str) -> float:
    """Bytes actually occupied, counting no file twice.

    Content-addressed caches are built out of links: HuggingFace keeps one copy
    in `blobs/` and links it into every `snapshots/` revision that uses it, and
    package managers hardlink across versions. Following links reported a 128 GB
    model cache as 289 GB — a 2.26x overstatement of the single largest number
    in the field, which is exactly the sort of confident wrongness that makes an
    instrument worth less than nothing.

    `lstat` handles symlinks; the inode set handles hardlinks. Both are needed,
    because which one a cache uses is a detail of the tool that built it.
    """
    total = 0
    seen: set[tuple[int, int]] = set()
    try:
        for dirpath, _, filenames in os.walk(path):
            for fn in filenames:
                try:
                    st = os.lstat(os.path.join(dirpath, fn))
                except OSError:
                    continue
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                total += st.st_size
            if total > 500 * 1024**3:
                break
    except OSError:
        return 0.0
    return total / 1024**3


def largest_caches(paths: dict[str, str], threshold_gb: float) -> list[tuple[str, float]]:
    """Named directories that have grown past a threshold.

    Package and model caches are the usual reason a drive fills without anyone
    changing anything, and they are invisible until someone goes looking.
    """
    import time as _t

    cached = _load_sizes()
    now = _t.time()
    changed = False
    out = []
    for label, path in paths.items():
        expanded = os.path.expandvars(path)
        if not os.path.isdir(expanded):
            continue
        hit = cached.get(label)
        if hit and now - hit.get("at", 0) < _SIZE_TTL:
            gb = hit["gb"]
        else:
            gb = _dir_size_gb(expanded)
            cached[label] = {"gb": gb, "at": now}
            changed = True
        if gb >= threshold_gb:
            out.append((label, gb))
    if changed:
        try:
            with open(_SIZE_CACHE, "w", encoding="utf-8") as fh:
                json.dump(cached, fh)
        except OSError:
            pass
    out.sort(key=lambda t: -t[1])
    return out


# Caches worth knowing the size of. Named by what they belong to rather than by
# path, because the path is an implementation detail of the tool that owns it.
KNOWN_CACHES = {
    "huggingface models": "%USERPROFILE%/.cache/huggingface",
    "ollama models": "%USERPROFILE%/.ollama/models",
    "pip": "%LOCALAPPDATA%/pip/Cache",
    "npm": "%APPDATA%/npm-cache",
    "cargo registry": "%USERPROFILE%/.cargo/registry",
    "gradle": "%USERPROFILE%/.gradle/caches",
    "pub (dart)": "%LOCALAPPDATA%/Pub/Cache",
    "nuget": "%USERPROFILE%/.nuget/packages",
    "docker": "%LOCALAPPDATA%/Docker",
}


def clear_caches() -> None:
    for fn in (pending_reboot, failed_services, scheduled_task_failures,
               resident_models):
        fn.cache_clear()
