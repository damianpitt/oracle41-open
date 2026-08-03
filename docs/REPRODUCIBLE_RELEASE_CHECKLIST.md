# Reproducible Debian Release Checklist

Use this checklist when cutting a public release.

## 1. Prepare Release Inputs

- Ensure the main branch is green in CI.
- Confirm `pyproject.toml` version matches the intended release.
- Confirm changelog/release notes are ready.
- Review `requirements/release-constraints.txt` for intentional dependency changes.
- Ensure working tree is clean.

## 2. Validate Locally

- Run tests:
  - `python3 -m pytest tests -q`
- Run lint/type checks:
  - `ruff check .`
  - `mypy src`
- Smoke-run app from source:
  - `QT_QPA_PLATFORM=offscreen oracle41-open --smoke-test`

## 3. Build Deterministic `.deb`

- Export deterministic timestamp from the release commit:
  - `export SOURCE_DATE_EPOCH="$(git log -1 --pretty=%ct)"`
- Build package:
  - `python3 -m pip install --constraint requirements/release-constraints.txt -e ".[packaging]"`
  - `./scripts/release/build_deb.sh`
- Verify output files exist:
  - `dist/oracle41-open_<version>_amd64.deb`
  - `dist/oracle41-open_<version>_amd64.deb.sha256`
  - `dist/oracle41-open_<version>_arm64.deb`
  - `dist/oracle41-open_<version>_arm64.deb.sha256`

The AMD64 and ARM64 packages must be built on native runners. Do not create an ARM64 package by overriding the architecture on an AMD64 host. Use Ubuntu 22.04 for AMD64 and Ubuntu 24.04 for ARM64; the pinned Qt ARM64 wheel requires `glibc 2.39` or newer.

## 4. Reproducibility Check

- Rebuild with the same `SOURCE_DATE_EPOCH` in a clean environment.
- Compare checksums:
  - `sha256sum -c dist/oracle41-open_<version>_amd64.deb.sha256`
  - `sha256sum -c dist/oracle41-open_<version>_arm64.deb.sha256`
- If checksums differ, inspect environment drift (Python version, pip resolver, OS image, tool versions).

## 5. Installation Smoke Test (Ubuntu/Debian)

- Install package:
  - `sudo apt install ./dist/oracle41-open_<version>_<arch>.deb`
- Fix dependency issues if needed:
  - `sudo apt-get -f install`
- Launch app:
  - `QT_QPA_PLATFORM=offscreen oracle41-open --smoke-test`
- Verify startup + tab navigation + provider key save.
- Verify double-click install path in Ubuntu App Center:
  - Open `.deb` in Files and confirm install succeeds without manual dependency fixes.
- Verify desktop launcher entry is present:
  - Search for `Oracle41 Open` in the applications menu.
- Repeat installation and desktop checks on native AMD64 and ARM64 systems before listing both architectures as validated. The initial ARM64 validation targets are Ubuntu 24.04 and Debian 13.

## 6. Publish

- Tag release commit (`vX.Y.Z`) and push tag.
- Confirm `Release Debian Package` workflow succeeds.
- Confirm GitHub release includes:
  - AMD64 `.deb` and `.deb.sha256` files
  - ARM64 `.deb` and `.deb.sha256` files
- Attach release notes with:
  - commit/tag
  - build timestamp (`SOURCE_DATE_EPOCH`)
  - checksum summary
