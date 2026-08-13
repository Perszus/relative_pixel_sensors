"""GRAMMAR probes — contents parsed as a language.

The jump text cannot make. A regex counts the word `function`; only a parser
knows a function is four hundred lines long, nested nine deep, takes eleven
arguments, or is never called from anywhere.

Python gets an exact parser for free from the stdlib. Everything else uses a
brace-and-indent reader, which is a heuristic and is labelled as one — it is
right about shape and wrong about anything that depends on semantics.

The governing rule for this kind: **a parse failure is not zero.** A parser that
chokes on one syntax version returns nothing, and nothing is indistinguishable
from clean. Every function here reports `None` when it could not read the file,
and the caller must treat that as unknown rather than as good news.
"""

from __future__ import annotations

import ast
import os
import re
import warnings
from functools import lru_cache

# Languages with a brace-delimited block structure. Indentation languages are
# handled separately because their nesting is whitespace.
_BRACED = (".rs", ".kt", ".java", ".dart", ".ts", ".tsx", ".js", ".jsx",
           ".cpp", ".cc", ".h", ".hpp", ".go", ".cs")

_FUNC_HINT = re.compile(
    r"^\s*(?:pub\s+|private\s+|public\s+|static\s+|async\s+|final\s+|override\s+)*"
    r"(?:fn|func|function|def|void|[A-Za-z_][\w<>,\[\]:\s]*?)\s+"
    r"([A-Za-z_]\w*)\s*\(")


class ParseFailure(Exception):
    """The file could not be read as its language. Not the same as clean."""


# --------------------------------------------------------------- python (exact)


@lru_cache(maxsize=2048)
def _py_tree(path: str) -> ast.AST:
    """Parse once per file per pass.

    Seven separate smell rules each asked for the same tree, so every Python
    file in the fleet was parsed seven times and a pass went from 3.3s to 9.5s.
    The cache is per-process and a pass is a process, so it cannot go stale.

    Warnings are suppressed because parsing someone else's source is not an
    occasion to lecture them: a file with an invalid escape sequence emits a
    SyntaxWarning per parse, and those were reaching the operator's terminal
    from a tool that is supposed to be silent.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(source)
    except (OSError, SyntaxError, ValueError, RecursionError) as e:
        raise ParseFailure(str(e)) from e


@lru_cache(maxsize=2048)
def py_functions(path: str) -> list[tuple[str, int, int, int]]:
    """(name, line, length, arg count) for every function in a Python file."""
    tree = _py_tree(path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            args = node.args
            n_args = (len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
                      + bool(args.vararg) + bool(args.kwarg))
            out.append((node.name, node.lineno, end - node.lineno + 1, n_args))
    return out


def py_max_depth(path: str) -> int:
    """Deepest nesting of control flow. Complexity you can feel while reading."""
    tree = _py_tree(path)
    nesting = (ast.If, ast.For, ast.While, ast.With, ast.Try,
               ast.AsyncFor, ast.AsyncWith)

    def depth(node, d=0):
        best = d
        for child in ast.iter_child_nodes(node):
            best = max(best, depth(child, d + 1 if isinstance(child, nesting) else d))
        return best

    return depth(tree)


def py_complexity(path: str) -> int:
    """Branch count across the file — cyclomatic complexity without the +1."""
    tree = _py_tree(path)
    branching = (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.With,
                 ast.Assert, ast.BoolOp, ast.IfExp, ast.comprehension)
    return sum(1 for n in ast.walk(tree) if isinstance(n, branching))


@lru_cache(maxsize=2048)
def py_smells(path: str) -> dict[str, int]:
    """Things a parser can see and a regex cannot.

    `except:` is findable by regex. A mutable default argument is not — it needs
    to know that the default is a literal list and that it belongs to a
    parameter.
    """
    tree = _py_tree(path)
    out = {"mutable_default": 0, "bare_except": 0, "star_import": 0,
           "assert_in_source": 0, "shadowed_builtin": 0, "broad_except": 0,
           "global_statement": 0, "nested_function_depth": 0}
    builtins = {"id", "list", "dict", "set", "type", "input", "filter", "map",
                "max", "min", "sum", "next", "object", "range", "str", "bytes"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + node.args.kw_defaults:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    out["mutable_default"] += 1
            if node.name in builtins:
                out["shadowed_builtin"] += 1
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                out["bare_except"] += 1
            elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
                out["broad_except"] += 1
        elif isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            out["star_import"] += 1
        elif isinstance(node, ast.Assert):
            out["assert_in_source"] += 1
        elif isinstance(node, ast.Global):
            out["global_statement"] += 1
    return out


def py_unused_privates(path: str) -> int:
    """Private functions defined and never referenced in the same file."""
    tree = _py_tree(path)
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name.startswith("_") and not n.name.startswith("__")}
    if not defined:
        return 0
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    return len(defined - used)


def py_api_surface(path: str) -> int:
    """Public names a module offers. A proxy for how much of it is load-bearing
    to everyone else."""
    tree = _py_tree(path)
    return sum(
        1 for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not n.name.startswith("_"))


def py_annotation_coverage(path: str) -> float:
    """Share of function parameters and returns that are annotated."""
    tree = _py_tree(path)
    total = annotated = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            total += 1
            annotated += node.returns is not None
            for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                if a.arg in ("self", "cls"):
                    continue
                total += 1
                annotated += a.annotation is not None
    return (annotated / total) if total else 1.0


# ------------------------------------------------------ everything else (shape)


@lru_cache(maxsize=2048)
def braced_functions(path: str) -> list[tuple[str, int, int, int]]:
    """(name, line, length, depth) by brace counting.

    A heuristic. It gets function extent right in ordinary code and wrong inside
    string literals containing braces; it is used for *shape* questions — how
    long, how deep — where being approximately right is the whole requirement.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as e:
        raise ParseFailure(str(e)) from e

    out: list[tuple[str, int, int, int]] = []
    depth = 0
    stack: list[tuple[str, int, int, int]] = []   # name, start, depth_at_open
    for i, raw in enumerate(lines, 1):
        line = re.sub(r"//.*$", "", raw)
        m = _FUNC_HINT.match(line)
        opens = line.count("{")
        closes = line.count("}")
        if m and opens:
            stack.append((m.group(1), i, depth, depth))
        depth += opens - closes
        if stack and depth <= stack[-1][2]:
            name, start, d0, maxd = stack.pop()
            out.append((name, start, i - start + 1, maxd - d0))
        elif stack:
            name, start, d0, maxd = stack[-1]
            stack[-1] = (name, start, d0, max(maxd, depth))
    return out


@lru_cache(maxsize=2048)
def max_indent_depth(path: str, tab_width: int = 4) -> int:
    """Deepest indentation, in levels. Language-agnostic nesting proxy."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            best = 0
            for line in fh:
                if not line.strip() or line.lstrip().startswith(("//", "#", "*")):
                    continue
                ws = len(line) - len(line.lstrip())
                best = max(best, (line[:ws].count("\t") * tab_width
                                  + line[:ws].count(" ")) // tab_width)
            return best
    except OSError as e:
        raise ParseFailure(str(e)) from e


def parse(path: str) -> str:
    """Which reader applies to this file: 'python', 'braced', or ''."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return "python"
    return "braced" if ext in _BRACED else ""
