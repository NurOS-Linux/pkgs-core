# NurOS Core Packages

Package build recipes for the NurOS Juldyz edition.

> [!NOTE]
> **Repository Mirrors**
>
> * **git.nuros.org** ([pkgs/core](https://git.nuros.org/pkgs/core)): primary, self hosted Forgejo instance, accounts restricted to the core team.
> * **GitHub** ([NurOS-Linux/pkgs-core](https://github.com/NurOS-Linux/pkgs-core)): mirror for external contributors. Issues and Pull Requests opened here are welcome and are reviewed and processed by the core team.

## Overview

This repository contains official package build recipes exclusively for the NurOS Juldyz edition.

All package recipes follow the PKGBUILD format. Packages are built into `.apg` packages using the `apgbuild` tool.

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

* `packages/<package_name>/template/`: contains the PKGBUILD recipe and build templates.
* `packages/<package_name>/files/`: contains additional source assets, configuration files, patches, and service files.

## License

All package recipes in this repository are licensed under the MIT License unless stated otherwise.
