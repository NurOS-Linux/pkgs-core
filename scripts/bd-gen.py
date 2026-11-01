#!/usr/bin/env python3
"""Generate the machine readable package database for the NurOS core repo.

External tools (package managers, GUI frontends, search indexes) need to know
which packages exist, which versions they are at and where the build recipe
lives. Reading that information by scanning the repository over HTTP means
hundreds of requests and a lot of parsing work on the client side.

This script walks the repository once, extracts the metadata of every recipe
and writes it into a single JSON file that is committed to the repository
root. Consumers then only need one GET request.

Usage:
    scripts/bd-gen.py                 # write packages.json in the repo root
    scripts/bd-gen.py --output FILE   # write to a different path
    scripts/bd-gen.py --check         # exit non-zero if the file is stale
    scripts/bd-gen.py --stdout        # print the database to stdout

Regenerate after changing recipes and commit the result so consumers only
need one GET to read the full package list. Recipes are trusted shell code:
metadata extraction sources them, but does not call build functions.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
DEFAULT_OUTPUT = "packages.json"

# BD_TEMPLATE_DIR is set by run_bash() so that the script body never has to
# contain the path (avoids every quoting headache with subprocess + bash -c).
EXTRACT_SCRIPT = r"""
set -euo pipefail
cd "$BD_TEMPLATE_DIR"
eval "$BD_METADATA" >&2
for v in pkgname pkgver pkgrel epoch pkgdesc url pkgtype arch license \
         depends optdepends makedepends conflicts provides replaces backup \
         source install tags; do
  if declare -p "$v" >/dev/null 2>&1; then
    declare -p "$v"
  fi
done
"""


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def find_recipes(root: str) -> List[str]:
    """Return all recipe paths, sorted by package name."""
    packages_dir = os.path.join(root, "packages")
    recipes: List[str] = []
    if not os.path.isdir(packages_dir):
        return recipes
    for name in sorted(os.listdir(packages_dir)):
        template_dir = os.path.join(packages_dir, name, "template")
        if not os.path.isdir(template_dir):
            continue
        for candidate in ("APGBUILD", "PKGBUILD"):
            path = os.path.join(template_dir, candidate)
            if os.path.isfile(path):
                recipes.append(path)
                break
    return recipes


# ---------------------------------------------------------------------------
# Bash execution
# ---------------------------------------------------------------------------

def run_bash(script: str, template_dir: str, preamble: str) -> str:
    """Source a recipe in bash and return the declare-p output."""
    env = {**os.environ, "BD_TEMPLATE_DIR": template_dir, "BD_METADATA": preamble}
    try:
        r = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script],
            env=env,
            cwd=template_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{template_dir}: {exc}") from exc
    if r.returncode != 0:
        raise RuntimeError(f"{template_dir}: {r.stderr.strip()}")
    return r.stdout


# ---------------------------------------------------------------------------
# declare -p parser
# ---------------------------------------------------------------------------

def _unescape_dq(text: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r",
                        "\\": "\\", '"': '"', "$": "$", "`": "`"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _parse_scalar(value: str) -> str:
    v = value.strip()
    if not v:
        return ""
    # $'...' form (bash ANSI-C quoting) — treat as literal for our purposes
    if v.startswith("$'") and v.endswith("'"):
        return v[2:-1]
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1]
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return _unescape_dq(v[1:-1])
    return v


def _parse_array(value: str) -> List[str]:
    """Parse the ([idx]="value" ...) form that bash emits for arrays."""
    v = value.strip()
    if not (v.startswith("(") and v.endswith(")")):
        return []
    inner = v[1:-1]
    items: List[str] = []
    i = 0
    n = len(inner)
    while i < n:
        # skip whitespace
        while i < n and inner[i].isspace():
            i += 1
        if i >= n:
            break
        # skip [idx]=
        if inner[i] == "[":
            while i < n and inner[i] != "]":
                i += 1
            i += 1  # skip ]
            if i < n and inner[i] == "=":
                i += 1
            continue
        # quoted element
        if inner[i] == '"':
            i += 1
            buf: List[str] = []
            while i < n:
                c = inner[i]
                if c == "\\" and i + 1 < n:
                    buf.append(inner[i + 1])
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                buf.append(c)
                i += 1
            items.append("".join(buf))
            continue
        # unquoted token
        start = i
        while i < n and not inner[i].isspace():
            i += 1
        items.append(inner[start:i])
    return items


def parse_declares(output: str) -> Dict[str, Any]:
    """
    Parse the multi-line output of declare -p.

    Bash prints each variable on one logical line, but the value of a
    multi-element array can contain embedded newlines inside the parentheses.
    We split on 'declare ' at the start of a line and then find the balanced
    end of the value, so multi-line arrays are handled correctly.
    """
    result: Dict[str, Any] = {}
    # Collect every logical "declare …" block.
    # Strategy: split the raw text at every occurrence of "declare " that
    # appears at the start of a line (after optional leading whitespace).
    raw_lines = re.split(r"(?m)^(?=declare )", output)
    for raw in raw_lines:
        raw = raw.strip()
        if not raw.startswith("declare "):
            continue
        # e.g.  declare -a depends=([0]="glibc" [1]="bash")
        #        declare -- pkgname="bzip2"
        rest = raw[len("declare "):].strip()
        m = re.match(r"^(--|-[a-zA-Z]+)\s+(.+)$", rest, re.DOTALL)
        if not m:
            continue
        flags, remainder = m.group(1), m.group(2)
        if "=" not in remainder:
            continue
        eq = remainder.index("=")
        name = remainder[:eq].strip()
        value = remainder[eq + 1:]
        if not name:
            continue
        if "a" in flags or "A" in flags:
            result[name] = _parse_array(value)
        else:
            result[name] = _parse_scalar(value)
    return result


# ---------------------------------------------------------------------------
# Single-recipe extraction
# ---------------------------------------------------------------------------

def extract(recipe_path: str, root: str) -> Dict[str, Any]:
    template_dir = os.path.dirname(recipe_path)
    # Repository recipes declare metadata before their first function.
    # Evaluate only that preamble: build function syntax is irrelevant to the
    # catalog, and build functions must never execute during indexing.
    with open(recipe_path, encoding="utf-8") as fh:
        text = fh.read()
    preamble = re.split(r"(?m)^\s*(?:function\s+)?[a-zA-Z_][a-zA-Z0-9_]*\s*\(\s*\)\s*\{", text, maxsplit=1)[0]
    # Descriptions are display text; some recipes contain Markdown backticks
    # inside double quotes. Preserve these instead of executing them.
    preamble = re.sub(r'(?m)^(pkgdesc=".*")$', lambda m: re.sub(r'(?<!\\)`', r'\\`', m[0]), preamble)
    output = run_bash(EXTRACT_SCRIPT, template_dir, preamble)
    v = parse_declares(output)

    def scalar(key: str) -> str:
        val = v.get(key)
        if isinstance(val, str):
            return val
        return ""

    def opt(key: str) -> Optional[str]:
        s = scalar(key)
        return s if s else None

    def array(key: str) -> List[str]:
        val = v.get(key)
        if isinstance(val, list):
            return [str(x) for x in val]
        if isinstance(val, str) and val:
            return [val]
        return []

    pkgname = opt("pkgname") or os.path.basename(os.path.dirname(template_dir))
    pkgver  = scalar("pkgver")
    if not pkgver or not scalar("pkgrel"):
        raise ValueError(f"{recipe_path}: missing pkgver/pkgrel or failed extraction")
    pkgrel  = scalar("pkgrel")
    epoch   = opt("epoch")

    version = f"{pkgver}-{pkgrel}" if pkgrel else pkgver
    if epoch and epoch not in ("", "0"):
        version = f"{epoch}:{version}"

    arch = array("arch")
    architecture = "all" if "any" in arch else (arch[0] if arch else None)

    package_dir = os.path.dirname(template_dir)
    files_dir   = os.path.join(package_dir, "files")
    local_files: List[str] = []
    if os.path.isdir(files_dir):
        local_files = sorted(
            f for f in os.listdir(files_dir) if not f.startswith(".")
        )

    # A name::URL entry is a remote download, not a local source.
    local_sources: List[str] = []
    for entry in array("source"):
        if "::" in entry:
            continue
        elif "://" not in entry and "$" not in entry:
            local_sources.append(entry)

    return {
        "name":          pkgname,
        "version":       version,
        "pkgver":        pkgver,
        "pkgrel":        pkgrel,
        "epoch":         epoch,
        "type":          opt("pkgtype") or "binary",
        "architecture":  architecture,
        "architectures": arch,
        "description":   scalar("pkgdesc"),
        "url":           opt("url"),
        "license":       array("license"),
        "tags":          array("tags"),
        "depends":       array("depends"),
        "optdepends":    array("optdepends"),
        "makedepends":   array("makedepends"),
        "conflicts":     array("conflicts"),
        "provides":      array("provides"),
        "replaces":      array("replaces"),
        "backup":        array("backup"),
        "install":       array("install"),
        "local_sources": local_sources,
        "source":        array("source"),
        "files":         local_files,
        # paths relative to the repo root so consumers can build raw URLs
        "recipe":   os.path.relpath(recipe_path,   root).replace(os.sep, "/"),
        "template": os.path.relpath(template_dir,  root).replace(os.sep, "/"),
    }


# ---------------------------------------------------------------------------
# Database assembly
# ---------------------------------------------------------------------------

def git_revision(root: str) -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def build_database(root: str) -> Dict[str, Any]:
    recipes  = find_recipes(root)
    packages: Dict[str, Any] = {}

    for recipe in recipes:
        try:
            info = extract(recipe, root)
        except Exception as exc:
            print(f"bd-gen: warning: failed to parse {recipe}: {exc}",
                  file=sys.stderr)
            raise
        name = info["name"]
        if name:
            if name in packages:
                raise ValueError(f"duplicate package name: {name}")
            packages[name] = info

    # provides index: virtual name -> [real package names]
    provides_index: Dict[str, List[str]] = {}
    for pkg_name, info in packages.items():
        for provided in info["provides"]:
            key = re.split(r"[<>=]", provided, maxsplit=1)[0].strip()
            if key:
                provides_index.setdefault(key, []).append(pkg_name)
    for key in provides_index:
        provides_index[key] = sorted(set(provides_index[key]))

    return {
        "schema":        SCHEMA_VERSION,
        "generated_by":  "scripts/bd-gen.py",
        "repository":    "pkgs/core",
        "recipe_base_url": "https://git.nuros.org/pkgs/core/raw/branch/main/",
        "package_count": len(packages),
        "packages":      packages,
        "provides":      provides_index,
    }


def render(db: Dict[str, Any]) -> str:
    return json.dumps(db, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    root = repo_root()

    ap = argparse.ArgumentParser(
        description="Generate the JSON package database for the NurOS core repo"
    )
    ap.add_argument("--output", default=os.path.join(root, DEFAULT_OUTPUT),
                    help="path of the generated database file")
    ap.add_argument("--check",  action="store_true",
                    help="exit 1 if the on-disk file is out of date")
    ap.add_argument("--stdout", action="store_true",
                    help="print the database to stdout, do not write a file")
    args = ap.parse_args()

    db      = build_database(root)
    content = render(db)

    if args.stdout:
        sys.stdout.write(content)
        return 0

    output = args.output
    if not os.path.isabs(output):
        output = os.path.join(root, output)

    if args.check:
        try:
            current = open(output, "r", encoding="utf-8").read()
        except OSError:
            print(f"bd-gen: {output} does not exist", file=sys.stderr)
            return 1
        if current != content:
            print(f"bd-gen: {output} is stale – run scripts/bd-gen.py",
                  file=sys.stderr)
            return 1
        print(f"bd-gen: {output} is up to date")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    print(f"bd-gen: wrote {output} ({db['package_count']} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
