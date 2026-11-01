# NurOS Core Packages

Package build recipes for the NurOS Juldyz edition.

> [!NOTE]
> **Repository Mirrors**
>
> * **git.nuros.org** ([pkgs/core](https://git.nuros.org/pkgs/core)): primary, self hosted Forgejo instance, accounts restricted to the core team.
> * **GitHub** ([NurOS-Linux/pkgs-core](https://github.com/NurOS-Linux/pkgs-core)): mirror for external contributors. Issues and Pull Requests opened here are welcome and are reviewed and processed by the core team.

## Overview

This repository contains official package build recipes exclusively for the NurOS Juldyz edition.

All package recipes follow the APGBUILD format. Packages are built into `.apg` packages using the `apgbuild` tool.

* **git.nuros.org** ([utils/apgbuild](https://git.nuros.org/utils/apgbuild)): primary repository.
* **GitHub** ([NurOS-Linux/apgbuild](https://github.com/NurOS-Linux/apgbuild)): mirror.

## Repository Structure

Package recipes reside in the `packages/` directory following this layout:

```text
packages/
  <package_name>/
    files/
    template/
```

* `packages/<package_name>/template/`: contains the APGBUILD recipe and build templates.
* `packages/<package_name>/files/`: contains additional source assets, configuration files, patches, and service files.

## Package database

[`packages.json`](packages.json) lists all recipes, versions, dependencies,
architectures and local assets. Clients can fetch the entire catalog in one
request:

```
https://git.nuros.org/pkgs/core/raw/branch/main/packages.json
```

The versioned schema (`schema: 1`) contains a `packages` object keyed by package
name and a `provides` index mapping virtual names to providers. Each entry has
`pkgver`, `pkgrel`, the full `version`, and a repository-relative `recipe` path.
Append that path to `recipe_base_url` to download the APGBUILD. The catalog
describes build recipes, not downloadable binary packages.

After editing recipes, regenerate and commit the catalog:

```sh
python3 scripts/bd-gen.py
python3 scripts/bd-gen.py --check
```

Requires Python 3 and Bash. Extraction evaluates the metadata preamble before
the first function in trusted recipes to resolve shell variables and arrays.
Markdown backticks in descriptions are preserved as text. Build functions are
not parsed or invoked. `--stdout` prints
JSON and `--output FILE` selects an alternative destination. Generation is
deterministic and fails on extraction errors or duplicate package names.

## Weekly automation

Two GitHub Actions workflows maintain the default branch:

* `.github/workflows/bump.yml`: Monday at 02:17 UTC, runs
  `python scripts/bump.py --bump` (the next available version, not `--latest`)
  and commits changed APGBUILD recipes.
* `.github/workflows/package-database.yml`: Tuesday at 02:17 UTC, tests the
  generator, regenerates and validates `packages.json`, and commits changes.

Both support manual runs through `workflow_dispatch`, share a concurrency
group to avoid overlapping writes, and skip commits when nothing changes.
Schedules become active once the workflows are on the default branch.
GitHub Actions needs permission to push to that branch. These workflows update
the GitHub repository; synchronization to the Forgejo primary is separate.

## License

All package recipes in this repository are licensed under the MIT License unless stated otherwise.
