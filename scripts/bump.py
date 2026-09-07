#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Set, Tuple

USER_AGENT = "NurOS-PkgBumper/1.0"
VALID_STATUSES = {"newest", "outdated", "legacy", "untrusted"}
IGNORE_STATUSES = {"incorrect", "ignored", "noscheme", "unique", "devel", "rolling"}

_REPOLOGY_CACHE: Dict[str, List[dict]] = {}


def parse_pkgbuild(pkgbuild_path: str) -> Dict[str, any]:
    data = {}
    if not os.path.isfile(pkgbuild_path):
        return data

    with open(pkgbuild_path, "r", encoding="utf-8") as f:
        content = f.read()

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-zA-Z0-9_]+)=(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2)
            val = val.split("#")[0].strip().strip("'\"")
            data[key] = val

    provides_list = []
    m_prov = re.search(r"provides=\s*\((.*?)\)", content, re.DOTALL)
    if m_prov:
        raw_items = re.findall(r"['\"]([^'\"]+)['\"]|(\S+)", m_prov.group(1))
        for q, u in raw_items:
            item = (q or u).split("=")[0].strip()
            if item and not item.endswith(".so"):
                provides_list.append(item)
    data["provides"] = provides_list

    return data


def normalize_version(v: str) -> str:
    v = v.strip().lstrip("v")
    v = v.replace("_p", "p").replace(".p", "p")
    return v


def split_version(version: str) -> List[Tuple[int, str]]:
    v = normalize_version(version)
    chunks = re.findall(r"(\d+|[a-zA-Z]+)", v)
    parsed = []
    for chunk in chunks:
        if chunk.isdigit():
            parsed.append((int(chunk), ""))
        else:
            parsed.append((0, chunk))
    return parsed


def sort_versions(versions: List[str]) -> List[str]:
    return sorted(versions, key=split_version)


def is_valid_release_version(v: str) -> bool:
    if not v:
        return False
    if v in ("9999", "master", "latest", "trunk", "HEAD"):
        return False
    if re.search(r"(\+git|\.git|\.r\d+|\+r\d+|9999|alpha|beta|rc|pre|dev)", v, re.IGNORECASE):
        return False
    if "-" in v:
        return False
    return True


def fetch_repology_project(project_name: str) -> List[dict]:
    if project_name in _REPOLOGY_CACHE:
        return _REPOLOGY_CACHE[project_name]

    url = f"https://repology.org/api/v1/project/{project_name}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            _REPOLOGY_CACHE[project_name] = data
            return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _REPOLOGY_CACHE[project_name] = []
            return []
        raise
    except Exception:
        _REPOLOGY_CACHE[project_name] = []
        return []


def analyze_repology_project(project_name: str) -> Tuple[Set[str], Set[str]]:
    data = fetch_repology_project(project_name)
    newest_versions = set()
    all_clean_versions = set()

    for entry in data:
        status = entry.get("status", "")
        version = entry.get("version", "")
        if not version or status in IGNORE_STATUSES:
            continue
        if not is_valid_release_version(version):
            continue

        if status in VALID_STATUSES:
            all_clean_versions.add(version)
        if status == "newest":
            newest_versions.add(version)

    return newest_versions, all_clean_versions


def score_candidate(
    curr_ver: str,
    pkgname: str,
    cand_name: str,
    newest: Set[str],
    all_versions: Set[str],
) -> int:
    if not all_versions and not newest:
        return -1

    norm_curr = normalize_version(curr_ver)
    norm_all = {normalize_version(v) for v in all_versions}
    norm_newest = {normalize_version(v) for v in newest}

    score = 0
    if norm_curr in norm_newest:
        score += 1500
    elif norm_curr in norm_all:
        score += 1000

    if newest:
        score += 100

    if cand_name == pkgname:
        score += 10

    return score


def resolve_project_versions(
    pkgname: str,
    curr_ver: str,
    provides: List[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    candidates = [pkgname] + [p for p in provides if p != pkgname]

    best_cand = None
    best_score = -1
    best_newest: Set[str] = set()
    best_all: Set[str] = set()

    for cand in candidates:
        newest, all_vers = analyze_repology_project(cand)
        score = score_candidate(curr_ver, pkgname, cand, newest, all_vers)
        if score > best_score:
            best_score = score
            best_cand = cand
            best_newest = newest
            best_all = all_vers

    if not best_cand or best_score < 0:
        return None, None, None

    sorted_newest = sort_versions(list(best_newest))
    sorted_all = sort_versions(list(best_all))

    official_latest = None
    if sorted_newest:
        norm_curr = normalize_version(curr_ver)
        matching = [v for v in sorted_newest if normalize_version(v) == norm_curr]
        if matching:
            official_latest = curr_ver
        else:
            official_latest = sorted_newest[-1]
    elif sorted_all:
        official_latest = sorted_all[-1]

    next_ver = None
    if official_latest and split_version(official_latest) > split_version(curr_ver):
        newer_candidates = [
            v for v in sorted_newest if split_version(v) > split_version(curr_ver)
        ]
        if newer_candidates:
            next_ver = newer_candidates[0]
        else:
            next_ver = official_latest

    return best_cand, official_latest, next_ver


def bump_pkgbuild(pkgbuild_path: str, new_version: str) -> bool:
    with open(pkgbuild_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_content, count_v = re.subn(
        r"^(pkgver=).*$",
        rf"\g<1>{new_version}",
        content,
        flags=re.MULTILINE,
    )
    new_content, count_r = re.subn(
        r"^(pkgrel=).*$",
        r"\g<1>1",
        new_content,
        flags=re.MULTILINE,
    )

    if count_v > 0:
        with open(pkgbuild_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    return False


def scan_packages(base_dir: str) -> List[Dict[str, any]]:
    pkgs = []
    packages_dir = os.path.join(base_dir, "packages")
    if not os.path.isdir(packages_dir):
        return pkgs

    for entry in sorted(os.listdir(packages_dir)):
        pkg_path = os.path.join(packages_dir, entry)
        if not os.path.isdir(pkg_path):
            continue

        pkgbuild_path = os.path.join(pkg_path, "template", "PKGBUILD")
        if os.path.isfile(pkgbuild_path):
            vars_dict = parse_pkgbuild(pkgbuild_path)
            pkgs.append(
                {
                    "name": entry,
                    "pkgname": vars_dict.get("pkgname", entry),
                    "pkgver": vars_dict.get("pkgver", ""),
                    "pkgrel": vars_dict.get("pkgrel", "1"),
                    "provides": vars_dict.get("provides", []),
                    "path": pkgbuild_path,
                }
            )
    return pkgs


def main():
    parser = argparse.ArgumentParser(
        description="NurOS core packages version checker & bumper via Repology"
    )
    parser.add_argument(
        "-p", "--package", help="Target specific package name (directory name)"
    )
    parser.add_argument(
        "--bump",
        choices=["latest", "next"],
        help="Bump mode: 'latest' (to newest upstream) or 'next' (to immediate next version)",
    )
    args = parser.parse_args()

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    packages = scan_packages(root_dir)

    if args.package:
        packages = [p for p in packages if p["name"] == args.package]
        if not packages:
            print(f"Error: package '{args.package}' not found", file=sys.stderr)
            sys.exit(1)

    print(f"Scanning {len(packages)} package(s)...\n")

    for pkg in packages:
        name = pkg["name"]
        curr_ver = pkg["pkgver"]

        matched_cand, latest, next_ver = resolve_project_versions(
            pkg["pkgname"], curr_ver, pkg["provides"]
        )

        cand_info = f" (via {matched_cand})" if matched_cand and matched_cand != name else ""
        print(f"[{name}]{cand_info} Current: {curr_ver}")
        if latest:
            print(f"  Repology latest: {latest}")
        if next_ver:
            print(f"  Repology next:   {next_ver}")
        if not latest and not next_ver:
            print(f"  Repology: no data found for '{name}'")

        if args.bump:
            target_version = latest if args.bump == "latest" else next_ver
            if not target_version:
                print("  -> No target version available to bump to.")
            elif normalize_version(target_version) == normalize_version(curr_ver):
                print(f"  -> Package is already at version {curr_ver}.")
            else:
                success = bump_pkgbuild(pkg["path"], target_version)
                if success:
                    print(
                        f"  -> BUMPED {curr_ver}-{pkg['pkgrel']} -> {target_version}-1 in {pkg['path']}"
                    )
                else:
                    print(f"  -> Failed to update {pkg['path']}", file=sys.stderr)

        print()


if __name__ == "__main__":
    main()
