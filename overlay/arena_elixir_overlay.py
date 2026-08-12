#!/usr/bin/env python3
"""Transparent, click-through 18x32 arena-grid calibration overlay for macOS."""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEvent,
    NSFloatingWindowLevel,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakePoint,
    NSMakeRect,
    NSPanel,
    NSOpenPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSTimer,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow,
)
from Foundation import NSObject, NSString
from objc import python_method, super as objc_super

import frida


COLUMNS = 18
ROWS = 32
MIN_WIDTH = 180.0
MIN_HEIGHT = 320.0
ELIXIR_BAR_HEIGHT = 28.0
ELIXIR_BAR_GAP = 4.0
RUNNING_APP = None


def resolve_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


class GridOverlayView(NSView):
    def initWithManager_(self, manager):
        self = objc_super(GridOverlayView, self).initWithFrame_(NSMakeRect(0, 0, 1, 1))
        if self is None:
            return None
        self.manager = manager
        self.drag_mode = None
        self.start_frame = None
        self.start_mouse = None
        return self

    def acceptsFirstMouse_(self, _event):
        return True

    def drawRect_(self, _dirty_rect):
        bounds = self.bounds()
        width = bounds.size.width
        height = bounds.size.height
        if width <= 0 or height <= 0:
            return

        color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.15, 0.95, 0.70, 0.58)
        color.setStroke()
        border = NSBezierPath.bezierPathWithRect_(bounds)
        border.setLineWidth_(2.0 if self.manager.calibration_mode else 1.0)
        border.stroke()

        grid = NSBezierPath.bezierPath()
        grid.setLineWidth_(0.7)
        for column in range(1, COLUMNS):
            x = width * column / COLUMNS
            grid.moveToPoint_(NSMakePoint(x, 0))
            grid.lineToPoint_(NSMakePoint(x, height))
        for row in range(1, ROWS):
            y = height * row / ROWS
            grid.moveToPoint_(NSMakePoint(0, y))
            grid.lineToPoint_(NSMakePoint(width, y))
        grid.stroke()

        now = time.monotonic()
        for marker in self.manager.markers:
            if marker["expires_at"] <= now:
                continue
            x = marker["column"] * width / COLUMNS
            y = (ROWS - marker["row"] - 1) * height / ROWS
            side = marker.get("side")
            if side == "self":
                fill_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.42, 1.0, 0.46)
                stroke_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.66, 1.0, 0.96)
                banner_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.03, 0.20, 0.62, 0.88)
            elif side == "opponent":
                fill_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.12, 0.10, 0.48)
                stroke_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.22, 0.16, 0.95)
                banner_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.60, 0.04, 0.03, 0.90)
            else:
                fill_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.75, 0.76, 0.80, 0.34)
                stroke_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.88, 0.89, 0.92, 0.82)
                banner_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.20, 0.21, 0.24, 0.84)

            fill_color.setFill()
            NSBezierPath.bezierPathWithRect_(NSMakeRect(x, y, width / COLUMNS, height / ROWS)).fill()
            stroke_color.setStroke()
            marker_path = NSBezierPath.bezierPathWithRect_(NSMakeRect(x + 1, y + 1, width / COLUMNS - 2, height / ROWS - 2))
            marker_path.setLineWidth_(2.0)
            marker_path.stroke()

            label = NSString.stringWithString_(marker["card_name"])
            attributes = {
                NSFontAttributeName: NSFont.systemFontOfSize_(10),
                NSForegroundColorAttributeName: NSColor.whiteColor(),
            }
            label_size = label.sizeWithAttributes_(attributes)
            banner_width = label_size.width + 10
            banner_height = label_size.height + 4
            banner_x = min(max(2, x + width / COLUMNS / 2 - banner_width / 2), width - banner_width - 2)
            banner_y = y + height / ROWS if y + height / ROWS + banner_height <= height else y - banner_height
            banner_color.setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(banner_x, banner_y, banner_width, banner_height), 3, 3
            ).fill()
            label.drawAtPoint_withAttributes_(NSMakePoint(banner_x + 5, banner_y + 2), attributes)

        if self.manager.calibration_mode:
            accent = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.70, 0.15, 0.85)
            accent.setFill()
            for x, y in ((0, 0), (width - 9, 0), (0, height - 9), (width - 9, height - 9)):
                NSBezierPath.bezierPathWithRect_(NSMakeRect(x, y, 9, 9)).fill()

    def mouseDown_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        bounds = self.bounds()
        edge = 12.0
        horizontal = "left" if point.x < edge else "right" if point.x > bounds.size.width - edge else None
        vertical = "bottom" if point.y < edge else "top" if point.y > bounds.size.height - edge else None
        self.drag_mode = (horizontal, vertical) if horizontal or vertical else ("move", None)
        self.start_frame = self.window().frame()
        self.start_mouse = self.window().convertPointToScreen_(event.locationInWindow())

    def mouseDragged_(self, event):
        if self.start_frame is None or self.start_mouse is None:
            return
        current = self.window().convertPointToScreen_(event.locationInWindow())
        dx = current.x - self.start_mouse.x
        dy = current.y - self.start_mouse.y
        frame = self.start_frame
        x, y = frame.origin.x, frame.origin.y
        width, height = frame.size.width, frame.size.height
        horizontal, vertical = self.drag_mode

        if horizontal == "move":
            x += dx
            y += dy
        elif horizontal == "left":
            width = max(MIN_WIDTH, width - dx)
            x = frame.origin.x + frame.size.width - width
        elif horizontal == "right":
            width = max(MIN_WIDTH, width + dx)

        if vertical == "bottom":
            height = max(MIN_HEIGHT, height - dy)
            y = frame.origin.y + frame.size.height - height
        elif vertical == "top":
            height = max(MIN_HEIGHT, height + dy)

        self.window().setFrame_display_(NSMakeRect(x, y, width, height), True)
        self.manager.update_status()

    def mouseUp_(self, _event):
        self.drag_mode = None
        self.start_frame = None
        self.start_mouse = None


class ElixirStatusView(NSView):
    def initWithManager_(self, manager):
        self = objc_super(ElixirStatusView, self).initWithFrame_(NSMakeRect(0, 0, 1, 1))
        if self is None:
            return None
        self.manager = manager
        return self

    def drawRect_(self, _dirty_rect):
        bounds = self.bounds()
        width = bounds.size.width
        height = bounds.size.height
        if width <= 0 or height <= 0:
            return

        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.04, 0.05, 0.07, 0.58).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.5, 0.5, width - 1, height - 1), 6, 6
        ).fill()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.78, 0.32, 0.96, 0.92).setStroke()
        outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.5, 0.5, width - 1, height - 1), 6, 6
        )
        outline.setLineWidth_(1.0)
        outline.stroke()

        mana = self.manager.opponent_elixir
        value_text = "--" if mana is None else f"{mana:.1f} / 10"
        label = NSString.stringWithString_(f"OPPONENT ELIXIR  {value_text}")
        attributes = {
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(12),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        label_size = label.sizeWithAttributes_(attributes)
        label.drawAtPoint_withAttributes_(
            NSMakePoint(10, max(2, (height - label_size.height) / 2)), attributes
        )

        if width < 310:
            return
        segment_width = 9.0
        segment_gap = 3.0
        total_width = 10 * segment_width + 9 * segment_gap
        start_x = width - total_width - 10
        segment_y = 7.0
        segment_height = height - 14.0
        level = 0.0 if mana is None else max(0.0, min(10.0, mana))
        for index in range(10):
            x = start_x + index * (segment_width + segment_gap)
            segment = NSMakeRect(x, segment_y, segment_width, segment_height)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.26, 0.27, 0.31, 0.82).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(segment, 2, 2).fill()
            fill = max(0.0, min(1.0, level - index))
            if fill <= 0:
                continue
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.82, 0.35, 0.98, 0.98).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, segment_y, segment_width * fill, segment_height), 2, 2
            ).fill()


class ElixirStatusPanel(NSPanel):
    def initWithManager_frame_(self, manager, frame):
        self = objc_super(ElixirStatusPanel, self).initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        if self is None:
            return None
        self.setOpaque_(False)
        self.setBackgroundColor_(NSColor.clearColor())
        self.setHasShadow_(False)
        self.setLevel_(NSStatusWindowLevel)
        self.setHidesOnDeactivate_(False)
        self.setIgnoresMouseEvents_(True)
        self.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        self.setReleasedWhenClosed_(False)
        self.setContentView_(ElixirStatusView.alloc().initWithManager_(manager))
        return self


class OverlayPanel(NSPanel):
    def initWithManager_frame_(self, manager, frame):
        self = objc_super(OverlayPanel, self).initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        if self is None:
            return None
        self.setOpaque_(False)
        self.setBackgroundColor_(NSColor.clearColor())
        self.setHasShadow_(False)
        self.setLevel_(NSStatusWindowLevel)
        self.setHidesOnDeactivate_(False)
        self.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        self.setReleasedWhenClosed_(False)
        self.setContentView_(GridOverlayView.alloc().initWithManager_(manager))
        return self


class SettingsController(NSObject):
    FIELDS = (
        ("adb", "ADB executable"),
        ("device_connect", "ADB connect address"),
        ("device_serial", "ADB device serial"),
        ("package", "Game package"),
        ("frida_host", "Frida local host"),
        ("frida_device_port", "Frida Android port"),
        ("frida_server", "Frida server binary"),
        ("apk", "APK (only needed for install)"),
    )

    def initWithManager_(self, manager):
        self = objc_super(SettingsController, self).init()
        if self is None:
            return None
        self.manager = manager
        self.fields = {}
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(80, 100, 640, 470),
            NSWindowStyleMaskTitled | NSWindowStyleMaskUtilityWindow,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Tencent Overlay Settings")
        self.window.setLevel_(NSStatusWindowLevel)
        self.window.setReleasedWhenClosed_(False)
        self._build()
        return self

    @python_method
    def _build(self):
        content = self.window.contentView()
        y = 410
        for index, (key, title) in enumerate(self.FIELDS):
            label = NSTextField.labelWithString_(title)
            label.setFrame_(NSMakeRect(18, y + 3, 174, 22))
            content.addSubview_(label)
            has_browser = key in {"adb", "frida_server", "apk"}
            field_width = 346 if has_browser else 426
            field = NSTextField.alloc().initWithFrame_(NSMakeRect(194, y, field_width, 24))
            content.addSubview_(field)
            self.fields[key] = field
            if has_browser:
                browse = NSButton.alloc().initWithFrame_(NSMakeRect(548, y - 1, 72, 26))
                browse.setTitle_("Browse")
                browse.setTag_(index)
                browse.setTarget_(self)
                browse.setAction_("browse:")
                content.addSubview_(browse)
            y -= 42

        self.status_label = NSTextField.labelWithString_("")
        self.status_label.setFrame_(NSMakeRect(18, 47, 602, 34))
        self.status_label.setLineBreakMode_(0)
        content.addSubview_(self.status_label)

        save = NSButton.alloc().initWithFrame_(NSMakeRect(392, 12, 110, 28))
        save.setTitle_("Save")
        save.setTarget_(self)
        save.setAction_("save:")
        content.addSubview_(save)

        save_connect = NSButton.alloc().initWithFrame_(NSMakeRect(510, 12, 110, 28))
        save_connect.setTitle_("Save & Connect")
        save_connect.setTarget_(self)
        save_connect.setAction_("saveAndConnect:")
        content.addSubview_(save_connect)

    @python_method
    def show(self):
        settings = self.manager.settings
        for key, _title in self.FIELDS:
            self.fields[key].setStringValue_(str(settings.get(key, "")))
        self.status_label.setStringValue_(f"Saved in {self.manager.settings_path}")
        self.window.makeKeyAndOrderFront_(None)

    @python_method
    def collect(self):
        return {key: self.fields[key].stringValue().strip() for key, _title in self.FIELDS}

    @python_method
    def validate(self, settings):
        adb = resolve_path(settings["adb"], self.manager.runtime_root)
        frida_server = resolve_path(settings["frida_server"], self.manager.runtime_root)
        if not adb.is_file():
            return f"ADB was not found: {adb}"
        if not settings["device_connect"] and not settings["device_serial"]:
            return "Enter an ADB connect address or device serial."
        if not settings["package"]:
            return "Game package cannot be empty."
        if ":" not in settings["frida_host"]:
            return "Frida host must include a port, for example 127.0.0.1:27042."
        if not settings["frida_device_port"].isdecimal():
            return "Frida Android port must be a number."
        if not frida_server.is_file():
            return f"Frida server was not found: {frida_server}"
        apk = settings["apk"]
        if apk and not resolve_path(apk, self.manager.runtime_root).exists():
            return "APK was not found. Leave it empty when the game is already installed."
        return None

    @python_method
    def save_values(self):
        settings = self.collect()
        error = self.validate(settings)
        if error:
            self.status_label.setStringValue_(error)
            return False
        self.manager.settings = settings
        self.manager.save_settings()
        self.status_label.setStringValue_("Settings saved.")
        self.manager.update_status()
        return True

    def save_(self, _sender):
        self.save_values()

    def browse_(self, sender):
        key = self.FIELDS[sender.tag()][0]
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        if panel.runModal():
            self.fields[key].setStringValue_(panel.URL().path())

    def saveAndConnect_(self, _sender):
        if self.save_values():
            self.window.orderOut_(None)
            self.manager.connect_runtime()


class PaletteController(NSObject):
    def initWithManager_(self, manager):
        self = objc_super(PaletteController, self).init()
        if self is None:
            return None
        self.manager = manager
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(24, 80, 300, 322),
            NSWindowStyleMaskTitled | NSWindowStyleMaskUtilityWindow,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Arena Grid Calibration")
        self.window.setLevel_(NSStatusWindowLevel)
        self.window.setReleasedWhenClosed_(False)
        self._build_controls()
        return self

    @python_method
    def _label(self, text, frame, size=12):
        label = NSTextField.labelWithString_(text)
        label.setFrame_(frame)
        label.setFont_(NSFont.systemFontOfSize_(size))
        self.window.contentView().addSubview_(label)
        return label

    @python_method
    def _button(self, title, frame, action):
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        self.window.contentView().addSubview_(button)
        return button

    @python_method
    def _build_controls(self):
        self.connection_label = self._label("", NSMakeRect(18, 275, 264, 22), 12)
        self.connect_button = self._button("Connect", NSMakeRect(18, 239, 128, 28), "connect:")
        self._button("Settings", NSMakeRect(154, 239, 128, 28), "openSettings:")
        self.mode_label = self._label("", NSMakeRect(18, 205, 264, 22), 13)
        self.frame_label = self._label("", NSMakeRect(18, 169, 264, 30), 11)
        self._label("Drag inside the grid to move. Drag its edge or corner to resize.", NSMakeRect(18, 143, 264, 20), 11)
        self.mode_button = self._button("", NSMakeRect(18, 102, 128, 28), "toggleCalibration:")
        self._button("Save Profile", NSMakeRect(154, 102, 128, 28), "saveProfile:")
        self._button("Load Profile", NSMakeRect(18, 66, 128, 28), "loadProfile:")
        self._button("Reset Grid", NSMakeRect(154, 66, 128, 28), "resetGrid:")
        self.flip_button = self._button("", NSMakeRect(18, 30, 128, 28), "toggleFlip:")
        self._button("Quit", NSMakeRect(204, 8, 78, 24), "quit:")

    @python_method
    def refresh(self):
        mode = "CALIBRATION: grid accepts mouse input" if self.manager.calibration_mode else "LOCKED: grid is click-through"
        self.mode_label.setStringValue_(mode)
        self.mode_button.setTitle_("Lock Overlay" if self.manager.calibration_mode else "Calibrate")
        self.flip_button.setTitle_("Unflip Arena" if self.manager.flip_view else "Flip Arena 180 deg")
        self.connection_label.setStringValue_(self.manager.connection_status)
        self.connect_button.setTitle_("Connecting..." if self.manager.connecting else "Reconnect" if self.manager.monitor else "Connect")
        self.connect_button.setEnabled_(not self.manager.connecting)
        frame = self.manager.overlay.frame()
        top = self.manager.screen.frame().size.height - (frame.origin.y + frame.size.height)
        self.frame_label.setStringValue_(f"x={frame.origin.x:.0f}  y={top:.0f}  width={frame.size.width:.0f}  height={frame.size.height:.0f}")

    def toggleCalibration_(self, _sender):
        self.manager.set_calibration_mode(not self.manager.calibration_mode)

    def connect_(self, _sender):
        self.manager.connect_runtime()

    def openSettings_(self, _sender):
        self.manager.settings_controller.show()

    def saveProfile_(self, _sender):
        self.manager.save_profile()

    def loadProfile_(self, _sender):
        self.manager.load_profile()

    def resetGrid_(self, _sender):
        self.manager.reset_frame()

    def toggleFlip_(self, _sender):
        self.manager.flip_view = not self.manager.flip_view
        self.manager.overlay.contentView().setNeedsDisplay_(True)
        self.manager.update_status()

    def quit_(self, _sender):
        NSApp.terminate_(None)


class OverlayEventPump(NSObject):
    def initWithManager_(self, manager):
        self = objc_super(OverlayEventPump, self).init()
        if self is None:
            return None
        self.manager = manager
        return self

    def tick_(self, _timer):
        self.manager.drain_events()


class QueueMonitor:
    def __init__(self, target: str, script_path: Path, host: str, events: queue.SimpleQueue):
        self.target = target
        self.script_path = script_path
        self.host = host
        self.events = events
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="queue-monitor", daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        session = None
        try:
            source = self.script_path.read_text(encoding="utf-8")
            device = frida.get_device_manager().add_remote_device(self.host)
            session = device.attach(int(self.target) if self.target.isdecimal() else self.target)
            script = session.create_script(source)

            def on_message(message, _data):
                if message.get("type") != "send":
                    return
                try:
                    payload = message["payload"]
                    event = json.loads(payload) if isinstance(payload, str) else payload
                except (TypeError, json.JSONDecodeError):
                    return
                if not isinstance(event, dict):
                    return
                if event.get("event") in {
                    "queue_deploy",
                    "elixir_sample",
                    "elixir_battle_changed",
                    "local_elixir_identity_resolved",
                    "elixir_player_discovered",
                }:
                    self.events.put(event)

            script.on("message", on_message)
            script.load()
            print("Queue monitor attached", flush=True)
            self.events.put({"event": "monitor_ready"})
            while not self.stop_event.wait(0.2):
                pass
        except Exception as error:
            print(f"Queue monitor error: {error}", file=sys.stderr, flush=True)
            self.events.put({"event": "monitor_error", "message": str(error)})
        finally:
            if session is not None:
                session.detach()

    def stop(self):
        self.stop_event.set()

class ArenaGridApp:
    def __init__(
        self,
        profile_path: Path,
        start_locked: bool,
        monitor_target: str | None,
        script_path: Path,
        runtime_root: Path,
        settings_path: Path,
        auto_connect: bool,
    ):
        self.profile_path = profile_path
        self.script_path = script_path
        self.runtime_root = runtime_root.resolve()
        self.settings_path = settings_path
        self.settings = self.load_settings()
        self.screen = NSScreen.mainScreen()
        self.calibration_mode = not start_locked
        self.flip_view = False
        self.opponent_elixir = None
        self.connecting = False
        self.connection_status = "Not connected"
        self.card_names = self.load_card_names()
        self.markers = []
        self.events = queue.SimpleQueue()
        self.monitor = None
        self.overlay = OverlayPanel.alloc().initWithManager_frame_(self, self.default_frame())
        self.elixir_panel = ElixirStatusPanel.alloc().initWithManager_frame_(
            self, NSMakeRect(0, 0, 1, ELIXIR_BAR_HEIGHT)
        )
        self.settings_controller = SettingsController.alloc().initWithManager_(self)
        self.palette = PaletteController.alloc().initWithManager_(self)
        self.load_profile(silent=True)
        self.set_calibration_mode(self.calibration_mode)
        self.overlay.orderFrontRegardless()
        self.elixir_panel.orderFrontRegardless()
        self.palette.window.makeKeyAndOrderFront_(None)
        self.update_status()
        self.event_pump = OverlayEventPump.alloc().initWithManager_(self)
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.03, self.event_pump, "tick:", None, True)
        if monitor_target:
            self.monitor = QueueMonitor(monitor_target, script_path, self.settings["frida_host"], self.events)
            self.monitor.start()
        elif auto_connect:
            self.connect_runtime()

    def default_frame(self):
        visible = self.screen.visibleFrame()
        height = min(visible.size.height * 0.82, 900.0)
        width = height * COLUMNS / ROWS
        return NSMakeRect(visible.origin.x + 80, visible.origin.y + 60, width, height)

    def default_settings(self):
        adb_candidates = (
            self.runtime_root / "tools/platform-tools/adb",
            self.runtime_root.parent / ".android/sdk/platform-tools/adb",
        )
        adb = next((path for path in adb_candidates if path.is_file()), None)
        if adb is None:
            found = shutil.which("adb")
            adb = Path(found) if found else adb_candidates[0]

        return {
            "adb": self.portable_path(adb),
            "device_connect": "127.0.0.1:5555",
            "device_serial": "127.0.0.1:5555",
            "package": "com.tencent.tmgp.supercell.clashroyale",
            "frida_host": "127.0.0.1:27042",
            "frida_device_port": "27042",
            "frida_server": self.portable_path(self.runtime_root / "tools/frida/frida-server-17.17.0-android-arm64"),
            "apk": "",
        }

    def portable_path(self, path: Path):
        path = path.expanduser().resolve()
        if path == self.runtime_root.parent or self.runtime_root.parent in path.parents:
            return os.path.relpath(path, self.runtime_root)
        return str(path)

    def load_settings(self):
        defaults = self.default_settings()
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                defaults.update({key: str(value) for key, value in saved.items() if key in defaults})
        except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError):
            pass
        return defaults

    def save_settings(self):
        for key in ("adb", "frida_server", "apk"):
            if self.settings.get(key):
                self.settings[key] = self.portable_path(resolve_path(self.settings[key], self.runtime_root))
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings, indent=2) + "\n", encoding="utf-8")
        print(f"Saved runtime settings: {self.settings_path}", flush=True)

    def connect_runtime(self):
        if self.connecting:
            return
        self.connecting = True
        self.connection_status = "Connecting to Android..."
        self.update_status()
        threading.Thread(target=self._connect_runtime, name="runtime-connect", daemon=True).start()

    @python_method
    def _connect_runtime(self):
        settings = dict(self.settings)
        adb = resolve_path(settings["adb"], self.runtime_root)
        frida_server = resolve_path(settings["frida_server"], self.runtime_root)
        apk = resolve_path(settings["apk"], self.runtime_root) if settings["apk"] else Path("")
        env = os.environ.copy()
        env.update({
            "CR_ADB": str(adb),
            "CR_ADB_CONNECT": settings["device_connect"],
            "CR_ADB_SERIAL": settings["device_serial"],
            "CR_PACKAGE": settings["package"],
            "CR_APK": str(apk),
            "CR_FRIDA_SERVER": str(frida_server),
            "CR_FRIDA_HOST": settings["frida_host"],
            "CR_FRIDA_DEVICE_PORT": settings["frida_device_port"],
            "CR_SKIP_EMULATOR": "1",
            "CR_BOOT_TIMEOUT": "20",
        })
        command = [str(self.runtime_root / "bin/start_android.sh")]
        lines = []
        try:
            process = subprocess.Popen(
                command,
                cwd=self.runtime_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert process.stdout is not None
            for line in process.stdout:
                line = line.strip()
                if line:
                    lines.append(line)
                    print(line, flush=True)
            result = process.wait()
            if result != 0:
                raise RuntimeError(lines[-1] if lines else f"startup exited with status {result}")

            pid_command = [str(adb)]
            effective_serial = settings["device_serial"] or settings["device_connect"]
            if effective_serial:
                pid_command.extend(["-s", effective_serial])
            pid_command.extend(["shell", "pidof", settings["package"]])
            pid = subprocess.check_output(pid_command, text=True).strip().split()[0]
            self.events.put({"event": "runtime_connected", "pid": pid})
        except Exception as error:
            self.events.put({"event": "runtime_error", "message": str(error)})

    @staticmethod
    def load_card_names():
        path = Path(__file__).with_name("card_catalog.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {int(item["id"]): item["name"] for item in data["items"]}
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {}

    def set_calibration_mode(self, enabled: bool):
        self.calibration_mode = enabled
        self.overlay.setIgnoresMouseEvents_(not enabled)
        self.overlay.contentView().setNeedsDisplay_(True)
        self.update_status()

    def update_status(self):
        if hasattr(self, "elixir_panel"):
            self.sync_elixir_panel()
        if hasattr(self, "palette"):
            self.palette.refresh()

    def sync_elixir_panel(self):
        frame = self.overlay.frame()
        status_frame = NSMakeRect(
            frame.origin.x,
            frame.origin.y + frame.size.height + ELIXIR_BAR_GAP,
            frame.size.width,
            ELIXIR_BAR_HEIGHT,
        )
        self.elixir_panel.setFrame_display_(status_frame, True)

    def profile_data(self):
        frame = self.overlay.frame()
        screen_frame = self.screen.frame()
        top = screen_frame.size.height - (frame.origin.y + frame.size.height)
        corners = [
            {"x": frame.origin.x, "y": top},
            {"x": frame.origin.x + frame.size.width, "y": top},
            {"x": frame.origin.x + frame.size.width, "y": top + frame.size.height},
            {"x": frame.origin.x, "y": top + frame.size.height},
        ]
        return {
            "version": 1,
            "grid": {"columns": COLUMNS, "rows": ROWS},
            "flip_view": self.flip_view,
            "screen": {"width": screen_frame.size.width, "height": screen_frame.size.height},
            "frame_top_left": {"x": frame.origin.x, "y": top, "width": frame.size.width, "height": frame.size.height},
            "corners": corners,
        }

    def save_profile(self):
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(json.dumps(self.profile_data(), indent=2) + "\n", encoding="utf-8")
        print(f"Saved overlay profile: {self.profile_path}", flush=True)

    def load_profile(self, silent=False):
        if not self.profile_path.exists():
            if not silent:
                print(f"No profile found: {self.profile_path}", flush=True)
            return
        try:
            data = json.loads(self.profile_path.read_text(encoding="utf-8"))
            saved = data["frame_top_left"]
            self.flip_view = bool(data.get("flip_view", False))
            screen_height = self.screen.frame().size.height
            frame = NSMakeRect(
                float(saved["x"]),
                screen_height - float(saved["y"]) - float(saved["height"]),
                float(saved["width"]),
                float(saved["height"]),
            )
            self.overlay.setFrame_display_(frame, True)
            self.update_status()
            if not silent:
                print(f"Loaded overlay profile: {self.profile_path}", flush=True)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            print(f"Could not load overlay profile: {error}", file=sys.stderr, flush=True)

    def reset_frame(self):
        self.overlay.setFrame_display_(self.default_frame(), True)
        self.update_status()

    def drain_events(self):
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.get("event") == "monitor_ready":
                self.connecting = False
                self.connection_status = "Connected"
                self.update_status()
                print("Queue overlay ready", flush=True)
                continue
            if event.get("event") == "runtime_connected":
                pid = str(event["pid"])
                if self.monitor is not None:
                    self.monitor.stop()
                self.connection_status = f"Attaching to PID {pid}..."
                self.monitor = QueueMonitor(pid, self.script_path, self.settings["frida_host"], self.events)
                self.monitor.start()
                self.update_status()
                continue
            if event.get("event") in {"runtime_error", "monitor_error"}:
                self.connecting = False
                message = str(event.get("message", "connection failed")).replace("\n", " ")
                self.connection_status = f"Error: {message[:48]}"
                self.update_status()
                continue
            if event.get("event") == "elixir_battle_changed":
                auto_flip = event.get("auto_flip")
                if isinstance(auto_flip, bool):
                    self.flip_view = auto_flip
                self.opponent_elixir = None
                self.markers = []
                self.overlay.contentView().setNeedsDisplay_(True)
                self.elixir_panel.contentView().setNeedsDisplay_(True)
                self.update_status()
                print(
                    f"[battle] state={event.get('battle_state')} "
                    f"local_team_side={event.get('local_team_side')} auto_flip={auto_flip}",
                    flush=True,
                )
                changed = True
                continue
            if event.get("event") == "elixir_sample":
                mana = event.get("mana")
                if event.get("side") == "opponent" and isinstance(mana, (int, float)):
                    self.opponent_elixir = max(0.0, min(10.0, float(mana)))
                    self.elixir_panel.contentView().setNeedsDisplay_(True)
                    changed = True
                continue
            if event.get("event") == "elixir_player_discovered":
                continue
            tile = event.get("target_tile_center", {})
            x, y = tile.get("x"), tile.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            column = int(x - 0.5)
            row = int(y - 0.5)
            if self.flip_view:
                column = COLUMNS - 1 - column
                row = ROWS - 1 - row
            if not 0 <= column < COLUMNS or not 0 <= row < ROWS:
                continue
            card_id = event.get("card_id")
            card_name = self.card_names.get(card_id, f"Card {card_id}")
            self.markers.append({
                "column": column,
                "row": row,
                "card_name": card_name,
                "side": event.get("side", "unknown"),
                "expires_at": time.monotonic() + 1.0,
            })
            print(
                f"[queue] {event.get('side', 'unknown')} {card_name} ({card_id}) tile=({x}, {y})",
                flush=True,
            )
            changed = True
        now = time.monotonic()
        active_markers = [marker for marker in self.markers if marker["expires_at"] > now]
        if len(active_markers) != len(self.markers):
            self.markers = active_markers
            changed = True
        if changed:
            self.overlay.contentView().setNeedsDisplay_(True)
            self.overlay.contentView().displayIfNeeded()
            self.elixir_panel.contentView().displayIfNeeded()


def main() -> int:
    global RUNNING_APP
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=Path(__file__).resolve().parents[1] / "config/arena_grid_profile.json")
    parser.add_argument("--locked", action="store_true", help="Start in click-through mode.")
    parser.add_argument("--monitor", action="store_true", help="Attach the local queue monitor and highlight queued tiles.")
    parser.add_argument("--auto-connect", action="store_true", help="Connect using saved runtime settings after opening the UI.")
    parser.add_argument("--target", default="nullsroyale.rel.free", help="Frida process name or PID used with --monitor.")
    parser.add_argument("--script", type=Path, default=Path(__file__).with_name("hook_queue_deploy.js"), help="Frida script used with --monitor.")
    parser.add_argument("--runtime-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--settings", type=Path, default=Path(__file__).resolve().parents[1] / "config/runtime_settings.json")
    args = parser.parse_args()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    RUNNING_APP = ArenaGridApp(
        args.profile,
        args.locked,
        args.target if args.monitor else None,
        args.script,
        args.runtime_root,
        args.settings,
        args.auto_connect,
    )
    app.activateIgnoringOtherApps_(True)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
