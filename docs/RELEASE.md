# Release Guide

## Release Inputs

Before a release:

1. Update the version in `pyproject.toml`.
2. Update `CHANGELOG.md` and release notes.
3. Confirm the public working tree contains no secrets or internal planning files.
4. Ensure the main branch CI checks are green.
5. Validate the target Ubuntu/Debian installation path.

## Quality Gate

```bash
make check
```

The GitHub release workflow repeats the quality checks before building the Debian package.

## Debian Build

```bash
python3 -m pip install --constraint requirements/release-constraints.txt -e ".[packaging]"
export SOURCE_DATE_EPOCH="$(git log -1 --pretty=%ct)"
./scripts/release/build_deb.sh
```

The build produces:

- `dist/oracle41-open_<version>_<arch>.deb`
- `dist/oracle41-open_<version>_<arch>.deb.sha256`

The build uses a self-contained PyInstaller runtime. Release dependency versions are pinned in `requirements/release-constraints.txt`; update that file deliberately when upgrading packaging or runtime dependencies.

## Package Verification

Validate package metadata and contents with `dpkg-deb --info`, `dpkg-deb --contents`, and `sha256sum -c`. The release workflow also validates the desktop and AppStream metadata, extracts the package, runs the frozen binary offscreen, installs the `.deb`, and repeats the smoke test through `/usr/bin/oracle41-open`.

On a clean Ubuntu or Debian VM, install with:

```bash
sudo apt install ./dist/oracle41-open_*.deb
oracle41-open
```

Verify startup, tab navigation, Settings, provider-key storage, backup/restore, and uninstall behavior.

## Tagging

Use a semver-compatible tag such as `v0.1.0-alpha.1`. Pushing a version tag triggers the Debian release workflow. Do not push a tag until the source commit and package have been reviewed.
