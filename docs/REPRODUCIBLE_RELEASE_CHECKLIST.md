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

## 4. Reproducibility Check

- Rebuild with the same `SOURCE_DATE_EPOCH` in a clean environment.
- Compare checksums:
  - `sha256sum -c dist/oracle41-open_<version>_amd64.deb.sha256`
- If checksums differ, inspect environment drift (Python version, pip resolver, OS image, tool versions).

## 5. Installation Smoke Test (Ubuntu/Debian)

- Install package:
  - `sudo dpkg -i dist/oracle41-open_<version>_amd64.deb`
- Fix dependency issues if needed:
  - `sudo apt-get -f install`
- Launch app:
  - `QT_QPA_PLATFORM=offscreen oracle41-open --smoke-test`
- Verify startup + tab navigation + provider key save.
- Verify double-click install path in Ubuntu App Center:
  - Open `.deb` in Files and confirm install succeeds without manual dependency fixes.
- Verify desktop launcher entry is present:
  - Search for `Oracle41 Open` in the applications menu.

## 6. Publish

- Tag release commit (`vX.Y.Z`) and push tag.
- Confirm `Release Debian Package` workflow succeeds.
- Confirm GitHub release includes:
  - `.deb` artifact
  - `.deb.sha256` checksum file
- Attach release notes with:
  - commit/tag
  - build timestamp (`SOURCE_DATE_EPOCH`)
  - checksum summary
