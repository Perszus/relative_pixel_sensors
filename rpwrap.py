"""Record the outcome of a command someone was going to run anyway.

    python rpwrap.py build -- cargo build --release
    python rpwrap.py test  -- pytest
    python rpwrap.py lint  -- ruff check .

Runs the command, passes its output and exit code straight through, and writes
what happened to `.rp/runs.json` beside the project. Nothing about the command
changes; the only difference is that afterwards there is a receipt.

This is how EXECUTION gets built without giving up the property everything else
rests on. The field never runs anything: it reads this file, which makes the
probe a VERDICT read, and the execution happens because a person or a build
system was doing it regardless.

Two failure modes it is built against:

  * **A wrapper that stops being used reads as success.** If someone builds
    from an IDE instead, no receipt is written, the last one ages, and the
    probe reports UNKNOWN rather than a pass. Absence of a failure is not
    evidence of a success.
  * **A receipt that outlives its code.** Each record carries the commit it was
    taken at, so a green build from forty commits ago cannot be mistaken for a
    green build now.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

RECORD_DIR = ".rp"
RECORD_FILE = "runs.json"
KEEP = 20


def _repo_root(start: str) -> str:
    cur = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start)
        cur = parent


def _head(root: str) -> str:
    try:
        p = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=15)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def record(root: str, label: str, code: int, seconds: float, tail: str) -> None:
    path = os.path.join(root, RECORD_DIR, RECORD_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as fh:
            runs = json.load(fh)
    except (OSError, ValueError):
        runs = []
    runs.append({
        "label": label,
        "code": code,
        "seconds": round(seconds, 2),
        "at": time.time(),
        "head": _head(root),
        # Enough of the tail to recognise the failure, never the whole log.
        "tail": tail[-400:] if code else "",
    })
    runs = runs[-KEEP:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(runs, fh, indent=1)
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    if "--" not in argv or len(argv) < 3:
        print(__doc__.strip().splitlines()[0])
        print("\nusage: python rpwrap.py <label> -- <command ...>")
        return 2
    split = argv.index("--")
    label = argv[0] if split > 0 else "run"
    command = argv[split + 1:]
    if not command:
        print("nothing to run")
        return 2

    root = _repo_root(os.getcwd())
    started = time.perf_counter()
    captured: list[str] = []
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                bufsize=1)
    except OSError as e:
        print(f"rpwrap: cannot run {command[0]}: {e}", file=sys.stderr)
        return 127

    # Pass output through live. A wrapper that swallows output is a wrapper
    # nobody keeps using, and a wrapper nobody uses records nothing.
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        captured.append(line)
        if len(captured) > 400:
            del captured[:200]
    code = proc.wait()
    record(root, label, code, time.perf_counter() - started, "".join(captured))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
