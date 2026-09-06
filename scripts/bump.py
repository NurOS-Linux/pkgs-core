#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

USER_AGENT = "NurOS-PkgBumper/1.0"


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
            val = val.strip("'\"")
            data[key] = val

    provides_list = []
    m_prov = re.search(r"provides=\(([^)]*)\)", content)
    if m_prov:
        raw_items = re.findall(r"['\"]([^'\"]+)['\"]|(\S+)", m_prov.group(1))
        for q, u in raw_items:
            item = (q or u).split("=")[0].strip()
            if item and not item.endswith(".so"):
                provides_list.append(item)
    data["provides"] = provides_list

    return data


def split_version(version: str) -> List[Tuple[int, str]]:
    chunks = re.findall(r"(\d+|[a-zA-Z]+)", version)
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


def get_repology_versions(project_name: str) -> Tuple[Optional[str], List[str]]:
    url = f"https://repology.org/api/v1/project/{project_name}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, []
        raise
    except Exception:
        return None, []

    newest_candidates = set()
    all_clean_versions = set()

    for entry in data:
        status = entry.get("status")
        version = entry.get("version")
        if not version or not is_valid_release_version(version):
            continue

        all_clean_versions.add(version)
        if status == "newest":
            newest_candidates.add(version)

    sorted_all = sort_versions(list(all_clean_versions))
    official_latest = None
    if newest_candidates:
        official_latest = sort_versions(list(newest_candidates))[-1]
    elif sorted_all:
        official_latest = sorted_all[-1]

    return official_latest, sorted_all


def find_next_version(current: str, all_versions: List[str]) -> Optional[str]:
    sorted_vers = sort_versions(all_versions)
    curr_key = split_version(current)

    for v in sorted_vers:
        if split_version(v) > curr_key:
            return v
    return None


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
        candidates = [pkg["pkgname"]] + [p for p in pkg["provides"] if p != pkg["pkgname"]]

        latest = None
        all_versions = []

        for cand in candidates:
            cand_latest, cand_all = get_repology_versions(cand)
            if not cand_latest:
                continue
            if split_version(cand_latest) >= split_version(curr_ver):
                latest = cand_latest
                all_versions = cand_all
                break
            if not latest or split_version(cand_latest) > split_version(latest):
                latest = cand_latest
                all_versions = cand_all

        next_ver = find_next_version(curr_ver, all_versions)

        print(f"[{name}] Current: {curr_ver}")
        if latest:
            print(f"  Repology latest: {latest}")
        if next_ver:
            print(f"  Repology next:   {next_ver}")
        if not latest and not next_ver:
            print(f"  Repology: no data found for '{name}'")

        if args.bump:
            target_version = latest if args.bump == "latest" else next_ver
            if not target_version:
                print(f"  -> No target version available to bump to.")
            elif target_version == curr_ver:
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
