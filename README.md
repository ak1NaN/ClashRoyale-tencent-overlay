# Tencent Queue Overlay for macOS

An isolated macOS overlay for the Tencent Clash Royale arm64 client. It shows
queued deployment tiles and the currently bound opponent elixir value on an
18x32 arena grid.

This repository contains only the runtime source. It does not include the game
APK, extracted game libraries, Android emulator images, user captures, or local
Python environments.

## Requirements

- macOS 12 or newer
- Python 3.12 or newer
- a rooted arm64 Android emulator
- the Tencent client installed by the user
- an ADB TCP endpoint, commonly `127.0.0.1:5555` on MuMu

The current offsets target the tested Tencent package:

```text
com.tencent.tmgp.supercell.clashroyale
```

Client updates may change native offsets and break event capture.

## First Run

1. Double-click `Setup.command`.
2. Wait for Python dependencies, Android Platform Tools, and Frida Server
   17.17.0 to finish downloading.
3. Start the rooted arm64 emulator and open the Tencent client.
4. Double-click `Tencent Overlay Launcher.command`.
5. Open `Settings` in the calibration window.
6. Confirm the ADB address, device serial, package, and Frida paths.
7. Click `Save & Connect`.

Future launches only require `Tencent Overlay Launcher.command`.

`Setup.command` downloads Android Platform Tools from Google and Frida Server
from the official Frida GitHub release. Downloaded tools and `.venv` are ignored
by Git and stay local.

## Settings

Machine-specific settings are written to `config/runtime_settings.json` and are
not committed. The UI supports:

- ADB executable
- ADB TCP connect address
- ADB device serial
- Android package name
- local Frida host and forwarded port
- Android Frida port
- arm64 Frida Server binary
- optional APK path when the package is not installed

Grid placement is written to `config/arena_grid_profile.json` and is also kept
local. The checked-in `.example.json` files provide portable defaults.

## Terminal Launch

```bash
./Setup.command
./run_overlay.sh
```

## Repository Layout

```text
Tencent Overlay Launcher.command  Finder launcher
Setup.command                     first-run environment setup
run_overlay.sh                    terminal launcher
bin/start_android.sh              ADB, package, Frida, and game startup
overlay/arena_elixir_overlay.py   macOS grid and Settings UI
overlay/hook_opponent_elixir.js   Frida queue and elixir probe
overlay/card_catalog.json         card display names
config/                            portable defaults and local settings
```

## Project Credits

This Tencent runtime was developed collaboratively as part of the broader
[Jason-XII/cr-memory-reader](https://github.com/Jason-XII/cr-memory-reader)
project. The original `queue_overlay` subtask provided the Null-server baseline;
the collaboration extended it with Tencent migration, battle scoping, team-side
orientation, opponent elixir binding, the Settings UI, and a portable launch
flow.

The collaborators can add an open-source license when they are ready to define
third-party reuse terms.

Use this project only on software and accounts you are authorized to test, and
review the game's terms before use.
