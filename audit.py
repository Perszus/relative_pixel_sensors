"""Check what the field claims against reality.

Deliberately does NOT import rp.sensors. Every fact here is re-derived by a
different route -- walking the filesystem instead of reading git's index,
parsing reports directly instead of through the sensor -- because an audit that
shares a code path with the thing it audits only proves the code is consistent
with itself.

Prints one line per claim: OK, WRONG, or UNCHECKED.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OK, BAD, SKIP = "OK   ", "WRONG", "  -  "
results: list[tuple[str, str]] = []


def check(verdict: str, claim: str, detail: str = "") -> None:
    results.append((verdict, claim))
    line = f"[{verdict}] {claim}"
    if detail:
        line += f"\n         {detail}"
    print(line)


def sh(repo: str, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, timeout=90, shell=False)
        return p.stdout if p.returncode == 0 else ""
    except Exception:
        return ""


def walk_files(root: str) -> list[str]:
    """Filesystem walk, not git ls-files — the independent route."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "build", "target",
                                    ".dart_tool", "__pycache__", ".venv")]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            out.append(rel)
    return out


def main() -> int:
    fleet = json.load(open(os.path.join(HERE, "fleet.json"), encoding="utf-8"))
    view = json.load(open(os.path.join(HERE, "view.json"), encoding="utf-8"))
    brief = open(os.path.join(HERE, "BRIEF.txt"), encoding="utf-8").read()
    by_label = {label: path for path, label in fleet.items()}
    regions = {g["name"]: g for g in view["groups"]}

    print("=" * 78)
    print("AUDIT — the field's claims vs independently derived facts")
    print(f"field collected {(time.time() - view.get('collected_at', 0))/60:.0f} "
          f"min ago · {len(regions)} regions · {len(fleet)} repos")
    print("=" * 78)

    # ---- 1. "no tests at all in N projects" ------------------------------
    m = re.search(r"no tests at all in \d+ of the (\d+) projects with (\d+)\+ "
                  r"source files: ([^(\n]+)", brief)
    if m:
        min_src, claimed = int(m.group(2)), \
            {s.strip() for s in m.group(3).split(",") if s.strip()}
        # Mirror the field's own exclusions or this compares two different
        # questions: purity's only tests live under archive/, and counting them
        # made the audit disagree with a field that was right.
        skip = re.compile(r"(^|/)(archive|archived|legacy|backup|backups|old|"
                          r"deprecated|third_party|vendor|node_modules)(/|$)")
        src_re = re.compile(r"\.(py|rs|dart|kt|java|cpp|cc|h|hpp|ts|tsx|js|go|rb|cs)$")
        actually_none = set()
        for label, repo in by_label.items():
            if not os.path.isdir(repo):
                continue
            files = [f for f in walk_files(repo) if not skip.search(f)]
            if len([f for f in files if src_re.search(f)]) < min_src:
                continue  # below the threshold the field states
            has_test = any(
                re.search(r"(^|/)(tests?|spec|__tests__)/", f) or
                re.search(r"(^|/)test_[^/]+\.(py|dart|rs|kt)$", f) or
                re.search(r"_test\.(py|dart|go|rs|kt)$|\.test\.(ts|js)$", f)
                for f in files)
            if not has_test:
                actually_none.add(label)
        verdict = OK if claimed == actually_none else BAD
        check(verdict, f"no-tests claim: {sorted(claimed)}",
              "" if verdict == OK else f"filesystem says: {sorted(actually_none)}")
    else:
        check(SKIP, "no-tests claim — pattern wording changed, regex did not match")

    # ---- 2. "nothing checks N projects automatically" --------------------
    m = re.search(r"nothing checks \d+ of \d+ projects automatically: ([^\n]+)", brief)
    if m:
        claimed = {s.strip() for s in m.group(1).replace("\n", " ").split(",") if s.strip()}
        actually = set()
        for label, repo in by_label.items():
            if not os.path.isdir(repo):
                continue
            wf = os.path.join(repo, ".github", "workflows")
            has_ci = os.path.isdir(wf) and any(
                f.endswith((".yml", ".yaml")) for f in os.listdir(wf))
            if not has_ci:
                actually.add(label)
        verdict = OK if claimed == actually else BAD
        check(verdict, f"no-CI claim covers {len(claimed)} projects",
              "" if verdict == OK else
              f"only-in-claim {sorted(claimed - actually)}, "
              f"missed {sorted(actually - claimed)}")

    # ---- 3. "never reviewed" ---------------------------------------------
    m = re.search(r"never reviewed: ([^—\n]+)", brief)
    if m:
        claimed = {s.strip() for s in m.group(1).split(",") if s.strip()}
        actually = {label for label, repo in by_label.items()
                    if os.path.isdir(repo)
                    and not os.path.isfile(os.path.join(repo, "ester_analysis.md"))}
        verdict = OK if claimed == actually else BAD
        check(verdict, f"never-reviewed claim: {sorted(claimed)}",
              "" if verdict == OK else f"on disk: {sorted(actually)}")

    # ---- 4. uncommitted files --------------------------------------------
    # Uncommitted-file counts change every time anyone saves. Comparing a field
    # collected minutes ago against live `git status` produces a mismatch that
    # says nothing about the field -- the first run of this check "failed"
    # because a test file had been created between collection and audit.
    age = time.time() - view.get("collected_at", 0)
    m = re.search(r"(\d+) uncommitted files in ([^\n]+)", brief)
    if m and age > 120:
        check(SKIP, f"uncommitted files — field is {age/60:.0f} min old and this "
                    f"claim changes on every save; re-run collect.py to check it")
    elif m:
        claimed_n, claimed_where = int(m.group(1)), \
            {s.strip() for s in m.group(2).split(",") if s.strip()}
        actual_n, actual_where = 0, set()
        for label, repo in by_label.items():
            if not os.path.isdir(repo):
                continue
            n = len([l for l in sh(repo, "status", "--porcelain").splitlines()
                     if l.strip()])
            if n:
                actual_n += n
                actual_where.add(label)
        verdict = OK if (claimed_n == actual_n and claimed_where == actual_where) else BAD
        check(verdict, f"uncommitted: {claimed_n} files across {len(claimed_where)} repos",
              "" if verdict == OK else
              f"git status says {actual_n} across {sorted(actual_where)}")

    # ---- 5. the top STALLED region ---------------------------------------
    m = re.search(r"STALLED[^\n]*\n\s+(\S+)\s+R\s*([\d.]+)\s+B\s*([\d.]+)", brief)
    if m:
        name, r_val, b_val = m.group(1), float(m.group(2)), float(m.group(3))
        label = name.split("/")[0]
        repo = by_label.get(label)
        sub = name.split("/", 1)[1] if "/" in name else ""
        if repo and os.path.isdir(repo):
            # Independent: has anything in that subtree been committed recently?
            recent = sh(repo, "log", "--since=14 days ago", "--pretty=format:%h",
                        "--", sub).strip()
            verdict = OK if (b_val == 0.0 and not recent) else BAD
            check(verdict,
                  f"top stalled region {name} really has no recent activity",
                  "" if verdict == OK else
                  f"git shows {len(recent.splitlines())} commits touching {sub} in 14d")

            # And do the claimed findings exist, in files that exist?
            report = os.path.join(repo, "ester_analysis.md")
            hits = 0
            if os.path.isfile(report):
                open_block = False
                for line in open(report, encoding="utf-8", errors="replace"):
                    s = line.strip()
                    if s.startswith("### "):
                        open_block = "[OPEN]" in s
                    elif s.startswith("area:") and open_block:
                        area = s[5:].strip().split(":")[0].strip()
                        if sub and area.startswith(sub):
                            exists = os.path.isfile(os.path.join(repo, area))
                            hits += 1 if exists else 0
                            if not exists:
                                check(BAD, f"finding points at a file that is gone: {area}")
                        open_block = False
            claimed_files = regions.get(name, {}).get("standing", 0)
            check(OK if hits else BAD,
                  f"{name}: {hits} open finding(s) resolve to files that exist",
                  "" if hits else "no findings resolved — routing or parsing is off")

    # ---- 6. every pointer in the view refers to a real file --------------
    missing, checked = [], 0
    for name, g in regions.items():
        label = name.split("/")[0]
        repo = by_label.get(label)
        if not repo or not os.path.isdir(repo):
            continue
        prefix = name.split("/", 1)[1] if "/" in name else ""
        for ptr, _ in g.get("pointers", []):
            path = re.sub(r"\s*\(\d+\s*[KM]B\)$", "", ptr)
            checked += 1
            full = os.path.join(repo, prefix, path) if prefix else os.path.join(repo, path)
            if not os.path.isfile(full):
                missing.append(f"{name} -> {ptr}")
    verdict = OK if not missing else BAD
    check(verdict, f"{checked} pointers resolve to real files",
          "" if verdict == OK else
          f"{len(missing)} do not, e.g. {missing[:3]}")

    # ---- 7. region sizes ---------------------------------------------------
    wrong = []
    for name, g in list(regions.items())[:200]:
        label = name.split("/")[0]
        repo = by_label.get(label)
        if not repo or not os.path.isdir(repo) or not g.get("size"):
            continue
        prefix = name.split("/", 1)[1] if "/" in name else ""
        base = os.path.join(repo, prefix) if prefix else repo
        if not os.path.isdir(base):
            wrong.append(f"{name}: directory does not exist")
    check(OK if not wrong else BAD, "every sized region maps to a real directory",
          "" if not wrong else str(wrong[:3]))

    # ---- 8. the load-bearing claim ---------------------------------------
    m = re.search(r"load-bearing and under pressure: (\S+) \(R[\d.]+, (\d+) regions", brief)
    if m:
        name, claimed_fan = m.group(1), int(m.group(2))
        label = name.split("/", 1)[0]
        repo = by_label.get(label)
        sub = name.split("/", 1)[1] if "/" in name else ""
        if repo and os.path.isdir(repo):
            # Independent: grep the repo for imports naming any file in that
            # region, and count how many *other* regions those importers sit in.
            leaf = sub.rsplit("/", 1)[-1] if sub else ""
            hits = sh(repo, "grep", "-I", "-l", "-E",
                      rf"(import|use|from).*{re.escape(leaf)}")
            importers = {h.strip() for h in hits.splitlines() if h.strip()}
            outside = {h for h in importers if not h.startswith(sub + "/")}
            verdict = OK if (claimed_fan > 0 and outside) else BAD
            check(verdict,
                  f"load-bearing claim for {name}: {claimed_fan} dependents",
                  "" if verdict == OK else
                  f"grep finds no importers outside the region")
        else:
            check(SKIP, "load-bearing claim — repo not on disk")

    print("\n" + "=" * 78)
    bad = sum(1 for v, _ in results if v == BAD)
    skipped = sum(1 for v, _ in results if v == SKIP)
    print(f"{len(results) - bad - skipped} verified, {bad} wrong, {skipped} unchecked")
    if skipped:
        print("An unchecked claim is not a passing one — a reworded pattern that "
              "no longer matches its own audit is exactly how coverage rots.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
