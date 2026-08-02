#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to build the .deb package." >&2
  exit 1
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
  echo "dpkg-deb is required to build the .deb package." >&2
  exit 1
fi

if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller is required. Install with: pip install --constraint requirements/release-constraints.txt -e '.[packaging]'" >&2
  exit 1
fi

PACKAGE_NAME="oracle41-open"
VERSION="${1:-$(python3 - <<'PY'
from pathlib import Path
import tomllib

pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
print(pyproject["project"]["version"])
PY
)}"
if command -v dpkg >/dev/null 2>&1; then
  DEFAULT_ARCH="$(dpkg --print-architecture)"
else
  DEFAULT_ARCH="amd64"
fi
ARCH="${2:-$DEFAULT_ARCH}"
OUTPUT_DIR="${3:-$ROOT_DIR/dist}"
BUILD_ROOT="$OUTPUT_DIR/deb-build"
PKG_DIR="$BUILD_ROOT/${PACKAGE_NAME}_${VERSION}_${ARCH}"
INSTALL_DIR="$PKG_DIR/usr/lib/${PACKAGE_NAME}"
BIN_DIR="$PKG_DIR/usr/bin"
APP_DIR="$PKG_DIR/usr/share/applications"
APPSTREAM_DIR="$PKG_DIR/usr/share/metainfo"
ICON_ROOT="$PKG_DIR/usr/share/icons/hicolor"
DOC_DIR="$PKG_DIR/usr/share/doc/${PACKAGE_NAME}"
DEBIAN_DIR="$PKG_DIR/DEBIAN"
DEB_PATH="$OUTPUT_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
PYINSTALLER_DIST="$BUILD_ROOT/pyinstaller-dist"
PYINSTALLER_WORK="$BUILD_ROOT/pyinstaller-work"
PYINSTALLER_SPEC="$BUILD_ROOT/pyinstaller-spec"

rm -rf "$BUILD_ROOT"
mkdir -p \
  "$INSTALL_DIR" \
  "$BIN_DIR" \
  "$APP_DIR" \
  "$APPSTREAM_DIR" \
  "$ICON_ROOT" \
  "$DOC_DIR" \
  "$DEBIAN_DIR" \
  "$OUTPUT_DIR"

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name "$PACKAGE_NAME" \
  --distpath "$PYINSTALLER_DIST" \
  --workpath "$PYINSTALLER_WORK" \
  --specpath "$PYINSTALLER_SPEC" \
  --hidden-import keyring.backends.SecretService \
  src/oracle41_open/app/main.py

cp -a "$PYINSTALLER_DIST/$PACKAGE_NAME/." "$INSTALL_DIR/"

cat > "$BIN_DIR/${PACKAGE_NAME}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/lib/oracle41-open/oracle41-open "$@"
EOF
chmod 0755 "$BIN_DIR/${PACKAGE_NAME}"

APP_ID="io.github.damianpitt.oracle41_open"
install -m 0644 "packaging/linux/${APP_ID}.desktop" "$APP_DIR/${APP_ID}.desktop"
install -m 0644 "packaging/linux/${APP_ID}.metainfo.xml" "$APPSTREAM_DIR/${APP_ID}.metainfo.xml"
cp -a packaging/linux/icons/hicolor/. "$ICON_ROOT/"

install -m 0644 LICENSE "$DOC_DIR/copyright"
install -m 0644 packaging/linux/icons/LICENSE-CC0.txt "$DOC_DIR/icon-license-CC0.txt"
install -m 0644 README.md "$DOC_DIR/README.md"
gzip -n -9 -c CHANGELOG.md > "$DOC_DIR/changelog.gz"
chmod 0644 "$DOC_DIR/changelog.gz"

cat > "$DEBIAN_DIR/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${VERSION}
Section: finance
Priority: optional
Architecture: ${ARCH}
Maintainer: Oracle41 Team <opensource@oracle41.dev>
Homepage: https://github.com/damianpitt/oracle41-open
Depends: libegl1, libgl1, libxkbcommon-x11-0, libdbus-1-3, libfontconfig1, libx11-xcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xfixes0, libxcb-xinerama0
Recommends: desktop-file-utils, hicolor-icon-theme, xdg-utils
Description: Oracle41 Open wallet analytics desktop app
 Linux-first local wallet analytics desktop app for EVM wallets.
EOF
chmod 0644 "$DEBIAN_DIR/control"

cat > "$DEBIAN_DIR/postinst" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache --quiet /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 0755 "$DEBIAN_DIR/postinst"

cat > "$DEBIAN_DIR/postrm" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache --quiet /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 0755 "$DEBIAN_DIR/postrm"

if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
  python3 - "$PKG_DIR" "$SOURCE_DATE_EPOCH" <<'PY'
from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
timestamp = int(sys.argv[2])
for path in [root, *root.rglob("*")]:
    try:
        os.utime(path, (timestamp, timestamp), follow_symlinks=False)
    except OSError:
        continue
PY
fi

dpkg-deb --root-owner-group --build "$PKG_DIR" "$DEB_PATH"
sha256sum "$DEB_PATH" > "${DEB_PATH}.sha256"

echo "Built package:"
echo "  ${DEB_PATH}"
echo "Checksum:"
echo "  ${DEB_PATH}.sha256"
