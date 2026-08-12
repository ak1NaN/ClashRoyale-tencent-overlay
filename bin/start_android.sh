#!/usr/bin/env bash
set -euo pipefail

: "${CR_ADB:?ADB path is missing}"
: "${CR_PACKAGE:?Game package is missing}"
: "${CR_FRIDA_SERVER:?Frida server path is missing}"

ADB="$CR_ADB"
CONNECT="${CR_ADB_CONNECT:-}"
SERIAL="${CR_ADB_SERIAL:-${CR_ADB_CONNECT:-}}"
PACKAGE="$CR_PACKAGE"
APK="${CR_APK:-}"
FRIDA_SERVER="$CR_FRIDA_SERVER"
FRIDA_HOST="${CR_FRIDA_HOST:-127.0.0.1:27042}"
FRIDA_DEVICE_PORT="${CR_FRIDA_DEVICE_PORT:-27042}"
BOOT_TIMEOUT="${CR_BOOT_TIMEOUT:-20}"

[[ -x "$ADB" ]] || { printf 'ADB is not executable: %s\n' "$ADB" >&2; exit 1; }
[[ -f "$FRIDA_SERVER" ]] || { printf 'Frida server not found: %s\n' "$FRIDA_SERVER" >&2; exit 1; }
[[ -n "$SERIAL" ]] || { printf 'ADB device serial is empty.\n' >&2; exit 1; }

adb() {
  "$ADB" -s "$SERIAL" "$@"
}

"$ADB" start-server >/dev/null
if [[ -n "$CONNECT" ]]; then
  printf '[1/4] Connecting Android device: %s\n' "$CONNECT"
  "$ADB" connect "$CONNECT" >/dev/null || true
fi

for _ in $(seq 1 "$BOOT_TIMEOUT"); do
  if [[ "$(adb get-state 2>/dev/null || true)" == "device" ]]; then
    break
  fi
  sleep 1
done
[[ "$(adb get-state 2>/dev/null || true)" == "device" ]] || {
  printf 'Android device is unavailable: %s\n' "$SERIAL" >&2
  exit 1
}

printf '[2/4] Checking package: %s\n' "$PACKAGE"
if ! adb shell pm path "$PACKAGE" >/dev/null 2>&1; then
  [[ -n "$APK" && -f "$APK" ]] || {
    printf 'Game is not installed. Select an APK in Settings or install it manually.\n' >&2
    exit 1
  }
  adb install -r "$APK"
fi

printf '[3/4] Starting Frida server\n'
adb root >/dev/null 2>&1 || true
for _ in $(seq 1 10); do
  if [[ "$(adb get-state 2>/dev/null || true)" == "device" ]]; then
    break
  fi
  [[ -n "$CONNECT" ]] && "$ADB" connect "$CONNECT" >/dev/null 2>&1 || true
  sleep 1
done
if ! adb shell id | tr -d '\r' | grep -q 'uid=0'; then
  printf 'The emulator ADB shell is not root. Enable root and retry.\n' >&2
  exit 1
fi
adb push "$FRIDA_SERVER" /data/local/tmp/frida-server >/dev/null
adb shell chmod 755 /data/local/tmp/frida-server >/dev/null
adb shell 'toybox pkill frida-server 2>/dev/null; true' >/dev/null
adb shell 'nohup /data/local/tmp/frida-server >/dev/null 2>&1 &' >/dev/null
sleep 1

LOCAL_PORT="${FRIDA_HOST##*:}"
[[ "$LOCAL_PORT" =~ ^[0-9]+$ && "$FRIDA_DEVICE_PORT" =~ ^[0-9]+$ ]] || {
  printf 'Frida ports must be numeric.\n' >&2
  exit 1
}
adb forward "tcp:$LOCAL_PORT" "tcp:$FRIDA_DEVICE_PORT" >/dev/null

PID="$(adb shell pidof "$PACKAGE" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
if [[ -z "$PID" ]]; then
  printf '[4/4] Starting game\n'
  LAUNCHER="$(adb shell cmd package resolve-activity --brief "$PACKAGE" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  if [[ "$LAUNCHER" == *"/"* ]]; then
    adb shell am start -n "$LAUNCHER" >/dev/null
  else
    adb shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
  fi
else
  printf '[4/4] Game already running PID=%s\n' "$PID"
fi
