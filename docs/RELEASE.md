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

The GitHub release workflow repeats the quality checks before building native AMD64 and ARM64 Debian packages. Each package is built, installed, and smoke-tested on a runner with the matching architecture. AMD64 builds use Ubuntu 22.04; ARM64 builds use Ubuntu 24.04 because the pinned Qt ARM64 wheel requires `glibc 2.39` or newer.

## Platform Targets

| Package architecture | Native CI runner | Compatibility target |
| --- | --- | --- |
| AMD64 | Ubuntu 22.04 AMD64 | Ubuntu 22.04+ and Debian 12+ |
| ARM64 | Ubuntu 24.04 ARM64 | Ubuntu 24.04+ and Debian 13+ |

CI validates package creation, installation, and headless startup on the listed Ubuntu runners. Debian versions and derivative distributions remain compatibility targets until they pass clean-system validation. ARM32 is not supported. See [SUPPORTED_PLATFORMS.md](SUPPORTED_PLATFORMS.md) for the public support policy.

## Debian Build

```bash
python3 -m pip install --constraint requirements/release-constraints.txt -e ".[packaging]"
export SOURCE_DATE_EPOCH="$(git log -1 --pretty=%ct)"
./scripts/release/build_deb.sh
```

The build produces:

- `dist/oracle41-open_<version>_<arch>.deb`
- `dist/oracle41-open_<version>_<arch>.deb.sha256`

The build uses a self-contained PyInstaller runtime. It must run natively on the target architecture; the script rejects attempts to label a package for a different architecture. Release dependency versions are pinned in `requirements/release-constraints.txt`; update that file deliberately when upgrading packaging or runtime dependencies.

## Package Verification

Validate package metadata and contents with `dpkg-deb --info`, `dpkg-deb --contents`, and `sha256sum -c`. The release workflow also validates the desktop and AppStream metadata, extracts each package, runs the frozen binary offscreen, installs the `.deb`, and repeats the smoke test through `/usr/bin/oracle41-open`.

On a clean Ubuntu or Debian VM, install with:

```bash
sudo apt install ./dist/oracle41-open_*.deb
oracle41-open
```

Verify startup, tab navigation, Settings, provider-key storage, backup/restore, and uninstall behavior on each target distribution before marking it as manually validated.

## Tagging

Use a semver-compatible tag such as `v0.2.0-alpha.1`. Pushing a version tag triggers the Debian release workflow. Do not push a tag until the source commit and package have been reviewed.

Python uses the PEP 440 version `0.3.0a3`. Debian package metadata and filenames normalize that pre-release to `0.3.0~a3`, so a later `0.3.0` package is considered a valid upgrade.
