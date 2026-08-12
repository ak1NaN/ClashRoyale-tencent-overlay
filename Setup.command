#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

FRIDA_VERSION="17.17.0"
PLATFORM_TOOLS_URL="https://dl.google.com/android/repository/platform-tools-latest-darwin.zip"
FRIDA_URL="https://github.com/frida/frida/releases/download/$FRIDA_VERSION/frida-server-$FRIDA_VERSION-android-arm64.xz"

command -v python3 >/dev/null || {
  printf 'Python 3 is required. Install Python 3.12 or newer, then retry.\n' >&2
  exit 1
}

printf '[1/3] Creating Python environment\n'
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

mkdir -p tools/frida downloads
if [[ ! -x tools/platform-tools/adb ]]; then
  printf '[2/3] Downloading Android Platform Tools\n'
  curl -fL "$PLATFORM_TOOLS_URL" -o downloads/platform-tools.zip
  rm -rf tools/platform-tools
  /usr/bin/ditto -x -k downloads/platform-tools.zip tools
fi

FRIDA_SERVER="tools/frida/frida-server-$FRIDA_VERSION-android-arm64"
if [[ ! -x "$FRIDA_SERVER" ]]; then
  printf '[3/3] Downloading Frida server %s\n' "$FRIDA_VERSION"
  curl -fL "$FRIDA_URL" -o downloads/frida-server.xz
  .venv/bin/python -c 'import lzma, pathlib; source=pathlib.Path("downloads/frida-server.xz"); target=pathlib.Path("tools/frida/frida-server-17.17.0-android-arm64"); target.write_bytes(lzma.decompress(source.read_bytes()))'
  chmod +x "$FRIDA_SERVER"
fi

chmod +x run_overlay.sh bin/start_android.sh 'Tencent Overlay Launcher.command' Setup.command
printf '\nSetup complete. Double-click Tencent Overlay Launcher.command.\n'
