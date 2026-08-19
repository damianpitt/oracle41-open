# Supported Platforms

Oracle41 Open is a Linux-first desktop application. Version `0.3.0a9` is an alpha release, so the distinction between CI validation and broader compatibility targets is important.

## Debian Package Matrix

| CPU architecture | Release suffix | Native CI validation | Compatibility target |
| --- | --- | --- | --- |
| AMD64 / x86-64 | `_amd64.deb` | Ubuntu 22.04 AMD64 | Ubuntu 22.04 or newer; Debian 12 or newer |
| ARM64 / AArch64 | `_arm64.deb` | Ubuntu 24.04 ARM64 | Ubuntu 24.04 or newer; Debian 13 or newer |

The release workflow builds each package on a native runner, verifies its metadata and checksum, extracts and starts the frozen application, installs the package with APT, and repeats the smoke test through the installed command.

The Debian versions in the table are compatibility targets, not declarations that every desktop environment and derivative has been tested. Other Debian-based distributions may work when they provide compatible system libraries, but they have not yet been formally validated.

## Choosing a Package

Check the Debian architecture name:

```bash
dpkg --print-architecture
```

Install the matching package:

```bash
sudo apt install ./oracle41-open_<version>_<arch>.deb
```

Common architecture mappings are:

- `x86_64` from `uname -m` corresponds to the `amd64` Debian package.
- `aarch64` from `uname -m` corresponds to the `arm64` Debian package.

Verify a downloaded checksum from the directory containing both files:

```bash
sha256sum -c oracle41-open_<version>_<arch>.deb.sha256
```

## ARM Scope

The ARM package supports 64-bit ARM operating systems only. ARM32 systems and ARMv7 packages are not supported. Potential devices include ARM64 desktops and Raspberry Pi 4 or 5 systems running a compatible 64-bit Ubuntu or Debian desktop, but representative hardware validation remains on the roadmap.

## Other Linux Distributions

The project does not currently publish RPM, Pacman, Flatpak, Snap, or AppImage packages. Users of other Linux distributions may install from source with Python 3.11 or newer, but those environments are not covered by the Debian package validation workflow.

## Reporting Compatibility

Compatibility reports should include:

- Distribution and version
- Desktop environment
- Output of `uname -m`
- Output of `dpkg --print-architecture`
- Package filename and checksum
- Installation and application logs with API keys removed

Use the repository issue tracker for reproducible compatibility problems. Do not include provider credentials, wallet secrets, or private backup data.
