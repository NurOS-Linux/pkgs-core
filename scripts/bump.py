#!/usr/bin/env python3
import argparse
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Set, Tuple

USER_AGENT = "NurOS-PkgBumper/1.0"
VALID_STATUSES = {"newest", "outdated", "legacy", "untrusted"}
IGNORE_STATUSES = {"incorrect", "ignored", "noscheme", "unique", "devel", "rolling"}

_REPOLOGY_CACHE: Dict[str, List[dict]] = {}
_ANITYA_CACHE: Dict[str, Tuple[Set[str], Set[str]]] = {}
_ARCH_CACHE: Dict[str, Tuple[Set[str], Set[str]]] = {}


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
        for g1, g2 in raw_items:
            item = g1 or g2
            item = item.split("=")[0].split("<")[0].split(">")[0].strip()
            if item and item not in provides_list:
                provides_list.append(item)

    data["provides_list"] = provides_list
    return data


def normalize_version(v: str) -> str:
    return re.sub(r"^[vV_rR]+", "", v).replace("_", ".")


def split_version(v: str) -> List:
    v_clean = normalize_version(v)
    parts = re.split(r"([0-9]+)", v_clean)
    res = []
    for p in parts:
        if not p:
            continue
        if p.isdigit():
            res.append(int(p))
        else:
            res.append(p)
    return res


def sort_versions(versions: List[str]) -> List[str]:
    def key_func(v):
        return split_version(v)

    return sorted(versions, key=key_func)


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
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            _REPOLOGY_CACHE[project_name] = data
            return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _REPOLOGY_CACHE[project_name] = []
            return []
        _REPOLOGY_CACHE[project_name] = []
        return []
    except Exception:
        _REPOLOGY_CACHE[project_name] = []
        return []


def fetch_anitya_project(project_name: str) -> Tuple[Set[str], Set[str]]:
    if project_name in _ANITYA_CACHE:
        return _ANITYA_CACHE[project_name]

    url = f"https://release-monitoring.org/api/v2/projects/?name={urllib.parse.quote(project_name)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    newest = set()
    all_clean = set()

    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data.get("items", []):
                if item.get("name", "").lower() == project_name.lower():
                    v = item.get("version")
                    if v and is_valid_release_version(v):
                        newest.add(v)
                        all_clean.add(v)
                    for sv in item.get("stable_versions", []) or item.get("versions", []):
                        if sv and is_valid_release_version(sv):
                            all_clean.add(sv)
    except Exception:
        pass

    _ANITYA_CACHE[project_name] = (newest, all_clean)
    return newest, all_clean


def fetch_arch_project(project_name: str) -> Tuple[Set[str], Set[str]]:
    if project_name in _ARCH_CACHE:
        return _ARCH_CACHE[project_name]

    url = f"https://archlinux.org/packages/search/json/?name={urllib.parse.quote(project_name)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    newest = set()
    all_clean = set()

    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data.get("results", []):
                if item.get("pkgname", "").lower() == project_name.lower():
                    v = item.get("pkgver")
                    if v and is_valid_release_version(v):
                        newest.add(v)
                        all_clean.add(v)
    except Exception:
        pass

    _ARCH_CACHE[project_name] = (newest, all_clean)
    return newest, all_clean


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

    if not newest_versions and not all_clean_versions:
        an_newest, an_all = fetch_anitya_project(project_name)
        newest_versions.update(an_newest)
        all_clean_versions.update(an_all)

        arch_newest, arch_all = fetch_arch_project(project_name)
        newest_versions.update(arch_newest)
        all_clean_versions.update(arch_all)

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

    if count_v == 0:
        return False

    with open(pkgbuild_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True


def scan_packages(base_dir: str) -> List[Tuple[str, str, Dict[str, any]]]:
    packages = []
    pattern = os.path.join(base_dir, "packages", "*", "template", "APGBUILD")
    for pkgbuild in sorted(glob.glob(pattern)):
        rel = os.path.relpath(pkgbuild, base_dir)
        pkg_dir = os.path.dirname(os.path.dirname(pkgbuild))
        pkg_name = os.path.basename(pkg_dir)
        data = parse_pkgbuild(pkgbuild)
        if data.get("pkgver"):
            packages.append((pkg_name, pkgbuild, data))
    return packages


def main():
    parser = argparse.ArgumentParser(
        description="NurOS core packages version checker & bumper via Repology/Anitya/Arch"
    )
    parser.add_argument(
        "--check",
        nargs="?",
        const="__ALL__",
        help="Check single package or all packages without bumping",
    )
    parser.add_argument(
        "--bump",
        nargs="?",
        const="__ALL__",
        help="Bump single package or all packages to next available version",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="When bumping, bump directly to latest instead of next stepwise version",
    )
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    packages = scan_packages(repo_root)

    target_name = args.check or args.bump
    is_bump_mode = bool(args.bump)

    if target_name and target_name != "__ALL__":
        packages = [p for p in packages if p[0] == target_name]
        if not packages:
            print(f"Error: package '{target_name}' not found.")
            sys.exit(1)

    print(f"Loaded {len(packages)} packages.")
    print("-" * 60)

    bumped_count = 0
    up_to_date_count = 0
    outdated_count = 0
    not_found_count = 0

    for name, path, data in packages:
        curr_ver = data["pkgver"]
        provides = data.get("provides_list", [])

        cand, latest, next_ver = resolve_project_versions(name, curr_ver, provides)

        if not cand or not latest:
            print(f"[-] {name:<18} {curr_ver:<12} (upstream data not found)")
            not_found_count += 1
            continue

        is_outdated = split_version(latest) > split_version(curr_ver)

        if not is_outdated:
            print(f"[=] {name:<18} {curr_ver:<12} (up-to-date)")
            up_to_date_count += 1
        else:
            target_ver = latest if args.latest else (next_ver or latest)
            status_str = f"outdated -> next: {next_ver} | latest: {latest}"
            print(f"[!] {name:<18} {curr_ver:<12} ({status_str})")
            outdated_count += 1

            if is_bump_mode:
                if bump_pkgbuild(path, target_ver):
                    print(f"    -> BUMPED to {target_ver}")
                    bumped_count += 1
                else:
                    print(f"    -> FAILED to update {path}")

    print("-" * 60)
    print(
        f"Summary: Up-to-date: {up_to_date_count} | Outdated: {outdated_count} | Not found: {not_found_count}"
    )
    if is_bump_mode:
        print(f"Total packages bumped: {bumped_count}")


if __name__ == "__main__":
    main()
