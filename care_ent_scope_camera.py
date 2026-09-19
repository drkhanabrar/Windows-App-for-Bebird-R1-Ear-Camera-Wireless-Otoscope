import os
import sys
import time
import socket
import struct
import threading
import subprocess
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import cv2
from PIL import Image, ImageTk, ImageDraw

APP_NAME = "CARE ENT Scope Camera"
APP_VERSION = "2.0.0"
CAMERA_IP_DEFAULT = "192.168.10.123"
CAMERA_PORT_DEFAULT = 8030
HDR = 51
STRIDE = 1388
TYPE_VIDEO = 3

BASE_DIR = Path.home() / "Documents" / "CARE ENT Scope"
CAPTURE_DIR = BASE_DIR / "Captures"
VIDEO_DIR = BASE_DIR / "Videos"
for folder in (CAPTURE_DIR, VIDEO_DIR):
    folder.mkdir(parents=True, exist_ok=True)

BG = "#07161C"
SURFACE = "#0B2028"
SURFACE_2 = "#102A33"
SURFACE_3 = "#14343D"
LINE = "#1C4049"
TEXT = "#EFFBFD"
MUTED = "#7EA5AD"
TEAL = "#35C4B8"
TEAL_DARK = "#1B7774"
TEAL_SOFT = "#163F43"
RED = "#E25B6B"
AMBER = "#E2B455"
GREEN = "#57D4A8"
BLACK = "#03090C"


def start_packet():
    b = bytearray(24)
    struct.pack_into("<H", b, 0, 0x9999)
    b[2] = 1
    return bytes(b)


def stop_packet():
    b = bytearray(24)
    struct.pack_into("<H", b, 0, 0x9999)
    b[2] = 2
    return bytes(b)


START = start_packet()
STOP = stop_packet()


class ScopeStream:
    """Bebird-type UDP/JPEG stream receiver based on the supplied verified protocol."""
    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = int(port)
        self.sock = None
        self.running = False
        self.latest = None
        self.lock = threading.Lock()
        self.last_frame_time = 0.0
        self.pkt_count = 0
        self.frame_count = 0
        self.decode_ok = 0
        self.last_len = 0
        self.latest_id = 0
        self.error_text = ""

    def start(self):
        if self.running:
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except OSError:
            pass
        self.sock.bind(("", 0))
        self.sock.settimeout(1.0)
        self.running = True
        threading.Thread(target=self._keepalive, daemon=True).start()
        threading.Thread(target=self._recv, daemon=True).start()

    def stop(self):
        self.running = False
        sock = self.sock
        self.sock = None
        if sock:
            try:
                sock.sendto(STOP, (self.ip, self.port))
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _keepalive(self):
        while self.running:
            try:
                if self.sock:
                    self.sock.sendto(START, (self.ip, self.port))
            except OSError as exc:
                self.error_text = str(exc)
            time.sleep(0.4)

    def _emit(self, chunks):
        if not chunks:
            return
        buf = bytearray()
        for idx in sorted(chunks):
            payload = chunks[idx]
            offset = (idx - 1) * STRIDE
            if len(buf) < offset + len(payload):
                buf.extend(b"\x00" * (offset + len(payload) - len(buf)))
            buf[offset:offset + len(payload)] = payload
        try:
            img = cv2.imdecode(np.frombuffer(bytes(buf), np.uint8), cv2.IMREAD_COLOR)
        except cv2.error:
            img = None
        if img is not None:
            self.decode_ok += 1
            with self.lock:
                self.latest = img
                self.latest_id += 1
                self.last_frame_time = time.time()

    def _recv(self):
        chunks = {}
        collecting = False
        while self.running:
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as exc:
                self.error_text = str(exc)
                break
            self.pkt_count += 1
            self.last_len = len(data)
            if len(data) < HDR:
                continue
            if struct.unpack_from("<H", data, 2)[0] != TYPE_VIDEO:
                continue
            idx = struct.unpack_from("<H", data, 33)[0]
            payload = data[HDR:]
            if idx <= 0:
                continue
            if idx == 1 and payload[:2] == b"\xff\xd8":
                if collecting:
                    self._emit(chunks)
                chunks = {1: payload}
                collecting = True
                self.frame_count += 1
            elif collecting:
                chunks[idx] = payload

    def get_frame(self):
        with self.lock:
            if self.latest is None:
                return None, self.latest_id
            return self.latest.copy(), self.latest_id


class ModernButton(tk.Frame):
    def __init__(self, parent, text, command=None, width=None, height=38, kind="secondary", icon=None):
        bg = SURFACE_3 if kind == "secondary" else (TEAL_DARK if kind == "primary" else "#5D2530")
        hover = "#1A444D" if kind == "secondary" else ("#2A8F89" if kind == "primary" else "#7A2F3D")
        fg = TEXT
        super().__init__(parent, bg=bg, highlightthickness=1, highlightbackground=LINE)
        self._bg = bg
        self._hover = hover
        self._command = command
        self.configure(cursor="hand2")
        if width:
            self.configure(width=width)
            self.pack_propagate(False)
        self.configure(height=height)

        self.label = tk.Label(self, text=((icon + "  ") if icon else "") + text, bg=bg, fg=fg,
                              font=("Arial", 10, "bold"), cursor="hand2")
        self.label.pack(fill="both", expand=True, padx=10)
        for w in (self, self.label):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<Button-1>", self._on_click)

    def _on_enter(self, _event):
        self.configure(bg=self._hover)
        self.label.configure(bg=self._hover)

    def _on_leave(self, _event):
        self.configure(bg=self._bg)
        self.label.configure(bg=self._bg)

    def _on_click(self, _event):
        if self._command:
            self._command()

    def set(self, text, kind=None):
        if kind:
            self._bg = SURFACE_3 if kind == "secondary" else (TEAL_DARK if kind == "primary" else "#5D2530")
            self._hover = "#1A444D" if kind == "secondary" else ("#2A8F89" if kind == "primary" else "#7A2F3D")
        self.label.configure(text=text, bg=self._bg)
        self.configure(bg=self._bg)


class CameraApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg=BG)
        self.minsize(1040, 650)
        self._start_window()

        self.stream = None
        self.last_frame = None
        self.display_image = None
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.drag_start = None
        self.freeze = False
        self.recording = False
        self.writer = None
        self.record_path = None
        self.record_started = 0.0
        self.record_frames = 0
        self.last_record_id = -1
        self.display_fps = 0.0
        self.last_frame_counter = 0
        self.last_fps_time = time.time()
        self.closed = False
        self.viewer_mode = "live"

        self.camera_ip = tk.StringVar(value=CAMERA_IP_DEFAULT)
        self.camera_port = tk.StringVar(value=str(CAMERA_PORT_DEFAULT))
        self.auto_start = tk.BooleanVar(value=True)
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.brightness_var = tk.IntVar(value=0)
        self.contrast_var = tk.DoubleVar(value=1.0)
        self.saturation_var = tk.DoubleVar(value=1.0)
        self.gamma_var = tk.DoubleVar(value=1.0)
        self.sharpness_var = tk.DoubleVar(value=0.0)
        self.denoise_var = tk.BooleanVar(value=False)
        self.flip_h = tk.BooleanVar(value=False)
        self.flip_v = tk.BooleanVar(value=False)
        self.rotate_var = tk.StringVar(value="0°")
        self.grid_var = tk.BooleanVar(value=False)
        self.crosshair_var = tk.BooleanVar(value=False)
        self.timestamp_var = tk.BooleanVar(value=False)
        self.safe_overlay_var = tk.BooleanVar(value=True)
        self.save_dir_var = tk.StringVar(value=str(CAPTURE_DIR))
        self.drawer_visible = tk.BooleanVar(value=False)

        self._build_styles()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Escape>", self._escape)
        self.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.bind("<space>", lambda _e: self.toggle_freeze())
        self.bind("<Control-s>", lambda _e: self.capture_snapshot())
        self.bind("<Control-r>", lambda _e: self.toggle_record())
        self.bind("<plus>", lambda _e: self.zoom_step(0.1))
        self.bind("<equal>", lambda _e: self.zoom_step(0.1))
        self.bind("<minus>", lambda _e: self.zoom_step(-0.1))
        self.after(40, self.ui_tick)
        if self.auto_start.get():
            self.after(350, self.connect_camera)

    def _start_window(self):
        try:
            self.state("zoomed")
        except tk.TclError:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"{min(1320, sw-30)}x{min(820, sh-70)}")

    def _build_styles(self):
        self.option_add("*Font", "Arial 10")
        self.option_add("*Entry.background", SURFACE_2)
        self.option_add("*Entry.foreground", TEXT)
        self.option_add("*Entry.insertBackground", TEXT)

    def _label(self, parent, text, size=10, color=TEXT, weight="normal", bg=None, anchor="w"):
        return tk.Label(parent, text=text, bg=bg or parent.cget("bg"), fg=color,
                        font=("Arial", size, "bold" if weight == "semibold" else "normal"), anchor=anchor)

    def _build_ui(self):
        # Header
        header = tk.Frame(self, bg=SURFACE, height=72)
        header.pack(fill="x")
        header.pack_propagate(False)

        brand = tk.Frame(header, bg=SURFACE)
        brand.pack(side="left", fill="y", padx=20)
        logo = tk.Canvas(brand, width=42, height=42, bg=SURFACE, highlightthickness=0)
        logo.pack(side="left", pady=14)
        logo.create_oval(3, 3, 39, 39, fill=TEAL_SOFT, outline=TEAL, width=2)
        logo.create_text(21, 21, text="C", fill=TEAL, font=("Arial", 17, "bold"))
        text_box = tk.Frame(brand, bg=SURFACE)
        text_box.pack(side="left", padx=(10, 0), pady=11)
        tk.Label(text_box, text="CARE ENT Scope", bg=SURFACE, fg=TEXT, font=("Arial", 19, "bold")).pack(anchor="w")
        tk.Label(text_box, text="PROFESSIONAL CAMERA WORKSTATION", bg=SURFACE, fg="#76B4BC", font=("Arial", 8)).pack(anchor="w", pady=(1,0))

        self.header_status = tk.Frame(header, bg=SURFACE)
        self.header_status.pack(side="right", fill="y", padx=18)
        self.connection_badge = tk.Label(self.header_status, text="● WAITING", bg=SURFACE, fg=AMBER, font=("Arial", 10, "bold"))
        self.connection_badge.pack(side="right", padx=(10, 0), pady=26)
        ModernButton(self.header_status, "About", self.show_about, width=96, height=38, icon="ⓘ").pack(side="right", padx=5, pady=17)
        ModernButton(self.header_status, "Full Screen", self.toggle_fullscreen, width=112, height=38, icon="⛶").pack(side="right", padx=5, pady=17)
        self.settings_button = ModernButton(self.header_status, "Controls", self.toggle_drawer, width=105, height=38, icon="☷")
        self.settings_button.pack(side="right", padx=5, pady=17)

        # Main area
        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True)
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)

        viewer_wrap = tk.Frame(main, bg=BLACK)
        viewer_wrap.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 0))
        viewer_wrap.grid_rowconfigure(0, weight=1)
        viewer_wrap.grid_columnconfigure(0, weight=1)

        self.video_canvas = tk.Canvas(viewer_wrap, bg=BLACK, highlightthickness=0, cursor="crosshair")
        self.video_canvas.grid(row=0, column=0, sticky="nsew")
        self.video_canvas.bind("<ButtonPress-1>", self.on_pan_start)
        self.video_canvas.bind("<B1-Motion>", self.on_pan_move)
        self.video_canvas.bind("<ButtonRelease-1>", self.on_pan_end)
        self.video_canvas.bind("<MouseWheel>", self.on_mouse_zoom)
        self.video_canvas.bind("<Double-Button-1>", lambda _e: self.set_zoom(1.0))

        # Viewer chrome
        top_chrome = tk.Frame(viewer_wrap, bg="#071217", height=44)
        top_chrome.place(relx=0.02, rely=0.018, relwidth=0.96, height=44)
        top_chrome.pack_propagate(False)
        self.viewer_title = tk.Label(top_chrome, text="LIVE VIEW", bg="#071217", fg=TEXT, font=("Arial", 9, "bold"))
        self.viewer_title.pack(side="left", padx=14)
        self.viewer_meta = tk.Label(top_chrome, text="No signal", bg="#071217", fg=MUTED, font=("Arial", 9))
        self.viewer_meta.pack(side="left", padx=8)
        self.viewer_rec = tk.Label(top_chrome, text="", bg="#071217", fg=RED, font=("Arial", 9, "bold"))
        self.viewer_rec.pack(side="right", padx=14)

        # Empty state
        self.empty_title = tk.Label(viewer_wrap, text="Camera ready", bg=BLACK, fg=TEXT, font=("Arial", 22, "bold"))
        self.empty_title.place(relx=0.5, rely=0.46, anchor="center")
        self.empty_sub = tk.Label(viewer_wrap, text="Connect to the Wi-Fi scope to start live viewing", bg=BLACK, fg=MUTED, font=("Arial", 10))
        self.empty_sub.place(relx=0.5, rely=0.515, anchor="center")
        ModernButton(viewer_wrap, "Connect Camera", self.connect_camera, width=175, height=40, kind="primary", icon="◉").place(relx=0.5, rely=0.58, anchor="center")

        # Bottom command deck
        deck = tk.Frame(main, bg=BG, height=92)
        deck.grid(row=1, column=0, sticky="ew", padx=14, pady=(10, 14))
        deck.pack_propagate(False)
        self._build_command_deck(deck)

        # Side control drawer (starts hidden)
        self.drawer = tk.Frame(main, bg=SURFACE, width=330, highlightthickness=1, highlightbackground=LINE)
        self.drawer_visible_state = False
        self.drawer.place_forget()

        # Footer
        footer = tk.Frame(self, bg="#061116", height=26)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self.status_label = tk.Label(footer, text="Ready", bg="#061116", fg=MUTED, anchor="w", font=("Consolas", 8))
        self.status_label.pack(side="left", fill="x", expand=True, padx=12)
        tk.Label(footer, text="Ctrl+S Snapshot   Ctrl+R Record   Space Freeze   F11 Fullscreen", bg="#061116", fg="#5E7E85", font=("Arial", 8)).pack(side="right", padx=12)

    def _build_command_deck(self, parent):
        left = tk.Frame(parent, bg=SURFACE)
        left.pack(side="left", fill="both", expand=False)
        left.configure(width=360)
        left.pack_propagate(False)
        tk.Label(left, text="CAPTURE", bg=SURFACE, fg=MUTED, font=("Arial", 8, "bold")).pack(anchor="w", padx=14, pady=(10,4))
        row = tk.Frame(left, bg=SURFACE)
        row.pack(fill="x", padx=12, pady=(0, 10))
        self.snapshot_button = ModernButton(row, "Snapshot", self.capture_snapshot, height=44, kind="primary", icon="◉")
        self.snapshot_button.pack(side="left", fill="x", expand=True, padx=(0,5))
        self.record_button = ModernButton(row, "Start Recording", self.toggle_record, height=44, icon="●")
        self.record_button.pack(side="left", fill="x", expand=True, padx=(5,0))

        center = tk.Frame(parent, bg=SURFACE)
        center.pack(side="left", fill="both", expand=True, padx=8)
        tk.Label(center, text="VIEW", bg=SURFACE, fg=MUTED, font=("Arial", 8, "bold")).pack(anchor="w", padx=12, pady=(10,4))
        row2 = tk.Frame(center, bg=SURFACE)
        row2.pack(fill="x", padx=12, pady=(0,10))
        for label, cmd, icon in [
            ("Fit", lambda: self.set_zoom(1.0), "⌂"),
            ("Zoom −", lambda: self.zoom_step(-0.1), "−"),
            ("Zoom +", lambda: self.zoom_step(0.1), "+"),
            ("Mirror", self.toggle_mirror, "↔"),
            ("Rotate", self.rotate_once, "⟳"),
            ("Freeze", self.toggle_freeze, "❚❚"),
        ]:
            ModernButton(row2, label, cmd, height=44, icon=icon).pack(side="left", fill="x", expand=True, padx=3)

        right = tk.Frame(parent, bg=SURFACE)
        right.pack(side="right", fill="both", expand=False)
        right.configure(width=250)
        right.pack_propagate(False)
        tk.Label(right, text="SESSION", bg=SURFACE, fg=MUTED, font=("Arial", 8, "bold")).pack(anchor="w", padx=14, pady=(10,4))
        row3 = tk.Frame(right, bg=SURFACE)
        row3.pack(fill="x", padx=12, pady=(0,10))
        ModernButton(row3, "Captures", lambda: self.open_folder(self.save_dir_var.get()), height=44, icon="▧").pack(side="left", fill="x", expand=True, padx=(0,4))
        ModernButton(row3, "Videos", lambda: self.open_folder(VIDEO_DIR), height=44, icon="▶").pack(side="left", fill="x", expand=True, padx=(4,0))

    def _section_title(self, parent, title, subtitle=None):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", padx=16, pady=(14,6))
        tk.Label(row, text=title.upper(), bg=SURFACE, fg=TEAL, font=("Arial", 9, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(row, text=subtitle, bg=SURFACE, fg=MUTED, font=("Arial", 8)).pack(anchor="w", pady=(2,0))

    def _entry(self, parent, label, variable, width=20):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", padx=16, pady=5)
        tk.Label(row, text=label, bg=SURFACE, fg=MUTED, font=("Arial", 9)).pack(side="left")
        entry = tk.Entry(row, textvariable=variable, width=width, bg="#07171D", fg=TEXT, relief="flat", insertbackground=TEXT,
                         font=("Arial", 9), highlightthickness=1, highlightbackground=LINE, highlightcolor=TEAL)
        entry.pack(side="right", ipady=5)
        return entry

    def _slider(self, parent, label, variable, lo, hi, resolution=0.1, fmt=None):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", padx=16, pady=4)
        top = tk.Frame(row, bg=SURFACE)
        top.pack(fill="x")
        tk.Label(top, text=label, bg=SURFACE, fg="#B5CDD2", font=("Arial", 9)).pack(side="left")
        value = tk.Label(top, text="", bg=SURFACE, fg=TEXT, font=("Arial", 9, "bold"))
        value.pack(side="right")
        def update(_=None):
            v = variable.get()
            value.config(text=(fmt(v) if fmt else str(v)))
        scale = tk.Scale(row, from_=lo, to=hi, resolution=resolution, orient="horizontal", variable=variable,
                         bg=SURFACE, fg=TEXT, troughcolor="#193C44", activebackground=TEAL,
                         highlightthickness=0, showvalue=False, borderwidth=0, command=lambda _v: update())
        scale.pack(fill="x")
        update()
        return scale

    def toggle_drawer(self):
        if self.drawer_visible_state:
            self.drawer.place_forget()
            self.drawer_visible_state = False
            self.settings_button.set("☷  Controls")
            return
        self._populate_drawer()
        self.drawer.place(relx=1.0, x=-14, y=14, anchor="ne", relheight=0.82)
        self.drawer_visible_state = True
        self.settings_button.set("✕  Close")
        self.drawer.lift()

    def _populate_drawer(self):
        for child in self.drawer.winfo_children():
            child.destroy()
        header = tk.Frame(self.drawer, bg=SURFACE_2, height=62)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="CONTROL CENTER", bg=SURFACE_2, fg=TEXT, font=("Arial", 13, "bold")).pack(anchor="w", padx=18, pady=(12,0))
        tk.Label(header, text="Camera connection, image and overlays", bg=SURFACE_2, fg=MUTED, font=("Arial", 8)).pack(anchor="w", padx=18)

        canvas = tk.Canvas(self.drawer, bg=SURFACE, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg=SURFACE)
        canvas.create_window((0,0), window=inner, anchor="nw", tags="inner")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure("inner", width=e.width))

        self._section_title(inner, "Connection", "Wi-Fi scope endpoint")
        self._entry(inner, "Camera IP", self.camera_ip)
        self._entry(inner, "UDP Port", self.camera_port, 8)
        btnrow = tk.Frame(inner, bg=SURFACE)
        btnrow.pack(fill="x", padx=16, pady=6)
        ModernButton(btnrow, "Connect", self.connect_camera, height=36, kind="primary", icon="◉").pack(side="left", fill="x", expand=True, padx=(0,4))
        ModernButton(btnrow, "Disconnect", self.disconnect_camera, height=36, icon="○").pack(side="left", fill="x", expand=True, padx=(4,0))
        self._check(inner, "Auto-connect on launch", self.auto_start)

        self._section_title(inner, "Image", "Software processing — camera hardware exposure is unchanged")
        self._slider(inner, "Brightness", self.brightness_var, -60, 60, 1, lambda v: f"{int(v):+d}")
        self._slider(inner, "Contrast", self.contrast_var, 0.5, 2.0, 0.1, lambda v: f"{v:.1f}×")
        self._slider(inner, "Saturation", self.saturation_var, 0.0, 2.0, 0.1, lambda v: f"{v:.1f}×")
        self._slider(inner, "Gamma", self.gamma_var, 0.5, 2.0, 0.1, lambda v: f"{v:.1f}")
        self._slider(inner, "Sharpness", self.sharpness_var, 0.0, 2.0, 0.1, lambda v: f"{v:.1f}")
        self._check(inner, "Soft denoise", self.denoise_var)

        self._section_title(inner, "Orientation", "Preview and capture transforms")
        self._check(inner, "Mirror horizontally", self.flip_h)
        self._check(inner, "Flip vertically", self.flip_v)
        rrow = tk.Frame(inner, bg=SURFACE)
        rrow.pack(fill="x", padx=16, pady=5)
        tk.Label(rrow, text="Rotation", bg=SURFACE, fg=MUTED, font=("Arial", 9)).pack(side="left")
        opts = ["0°", "90°", "180°", "270°"]
        menu = tk.OptionMenu(rrow, self.rotate_var, *opts)
        menu.config(bg=SURFACE_3, fg=TEXT, activebackground=TEAL_DARK, activeforeground=TEXT, relief="flat", highlightthickness=0)
        menu["menu"].config(bg=SURFACE_3, fg=TEXT, activebackground=TEAL_DARK, activeforeground=TEXT)
        menu.pack(side="right")
        btnrow2 = tk.Frame(inner, bg=SURFACE)
        btnrow2.pack(fill="x", padx=16, pady=(6,4))
        ModernButton(btnrow2, "Fit", lambda: self.set_zoom(1.0), height=34, icon="⌂").pack(side="left", fill="x", expand=True, padx=(0,3))
        ModernButton(btnrow2, "Reset", self.reset_image, height=34, icon="↺").pack(side="left", fill="x", expand=True, padx=(3,0))

        self._section_title(inner, "Overlays", "Visual aids are included in display and capture")
        self._check(inner, "Composition grid", self.grid_var)
        self._check(inner, "Crosshair", self.crosshair_var)
        self._check(inner, "Timestamp", self.timestamp_var)
        self._check(inner, "Viewer chrome", self.safe_overlay_var)

        self._section_title(inner, "Session", "Local workstation storage")
        ModernButton(inner, "Choose Capture Folder", self.choose_save_folder, height=36, icon="▧").pack(fill="x", padx=16, pady=4)
        tk.Label(inner, textvariable=self.save_dir_var, bg=SURFACE, fg=MUTED, justify="left", wraplength=275,
                 font=("Arial", 8)).pack(fill="x", padx=18, pady=(0,6))
        ModernButton(inner, "Reset All Controls", self.reset_image, height=36, icon="↺").pack(fill="x", padx=16, pady=4)
        tk.Label(inner, text="", bg=SURFACE, height=2).pack()

    def _check(self, parent, text, variable):
        cb = tk.Checkbutton(parent, text=text, variable=variable, bg=SURFACE, fg="#B5CDD2", activebackground=SURFACE,
                            activeforeground=TEXT, selectcolor=TEAL_DARK, font=("Arial", 9), anchor="w", relief="flat")
        cb.pack(fill="x", padx=16, pady=3)
        return cb

    def _escape(self, _event):
        if self.drawer_visible_state:
            self.drawer.place_forget()
            self.drawer_visible_state = False
            self.settings_button.set("☷  Controls")
        else:
            self.exit_fullscreen()

    def connect_camera(self):
        try:
            ip = self.camera_ip.get().strip()
            port = int(self.camera_port.get())
            socket.inet_aton(ip)
            if not 1 <= port <= 65535:
                raise ValueError
        except Exception:
            messagebox.showerror("Camera Connection", "Please enter a valid IPv4 address and UDP port.", parent=self)
            return
        if self.stream:
            self.stream.stop()
        self.stream = ScopeStream(ip, port)
        self.stream.start()
        self.connection_badge.config(text="● CONNECTING", fg=AMBER)
        self.status_label.config(text=f"Listening for {ip}:{port} …")
        self.empty_title.config(text="Waiting for camera")
        self.empty_sub.config(text="Make sure the PC is connected to the scope Wi-Fi network")

    def disconnect_camera(self):
        if self.recording:
            self.stop_recording()
        if self.stream:
            self.stream.stop()
            self.stream = None
        self.connection_badge.config(text="● OFFLINE", fg="#C16C79")
        self.status_label.config(text="Camera disconnected")
        self.viewer_meta.config(text="No signal")

    def toggle_freeze(self):
        self.freeze = not self.freeze
        self.viewer_title.config(text="FROZEN FRAME" if self.freeze else "LIVE VIEW", fg=AMBER if self.freeze else TEXT)
        self.status_label.config(text="Display frozen" if self.freeze else "Live display resumed")

    def set_zoom(self, value):
        self.zoom = float(max(0.5, min(4.0, value)))
        self.zoom_var.set(self.zoom)
        if self.zoom <= 1.0:
            self.pan_x = self.pan_y = 0

    def zoom_step(self, delta):
        self.set_zoom(round(float(self.zoom_var.get()) + delta, 1))

    def on_mouse_zoom(self, event):
        self.zoom_step(0.1 if event.delta > 0 else -0.1)

    def on_pan_start(self, event):
        if self.zoom <= 1.0:
            return
        self.drag_start = (event.x, event.y, self.pan_x, self.pan_y)

    def on_pan_move(self, event):
        if not self.drag_start or self.zoom <= 1.0:
            return
        sx, sy, px, py = self.drag_start
        self.pan_x = px + (event.x - sx)
        self.pan_y = py + (event.y - sy)

    def on_pan_end(self, _event):
        self.drag_start = None

    def toggle_mirror(self):
        self.flip_h.set(not self.flip_h.get())

    def rotate_once(self):
        vals = ["0°", "90°", "180°", "270°"]
        idx = vals.index(self.rotate_var.get())
        self.rotate_var.set(vals[(idx + 1) % len(vals)])

    def toggle_fullscreen(self):
        now = bool(self.attributes("-fullscreen"))
        self.attributes("-fullscreen", not now)

    def exit_fullscreen(self):
        self.attributes("-fullscreen", False)

    def reset_image(self):
        self.brightness_var.set(0)
        self.contrast_var.set(1.0)
        self.saturation_var.set(1.0)
        self.gamma_var.set(1.0)
        self.sharpness_var.set(0.0)
        self.denoise_var.set(False)
        self.flip_h.set(False)
        self.flip_v.set(False)
        self.rotate_var.set("0°")
        self.grid_var.set(False)
        self.crosshair_var.set(False)
        self.timestamp_var.set(False)
        self.set_zoom(1.0)
        self.status_label.config(text="Image and view controls reset")

    def process_frame(self, frame, include_overlays=True):
        img = frame.copy()
        if self.flip_h.get() and self.flip_v.get():
            img = cv2.flip(img, -1)
        elif self.flip_h.get():
            img = cv2.flip(img, 1)
        elif self.flip_v.get():
            img = cv2.flip(img, 0)

        rot = self.rotate_var.get()
        if rot == "90°":
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif rot == "180°":
            img = cv2.rotate(img, cv2.ROTATE_180)
        elif rot == "270°":
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

        img = cv2.convertScaleAbs(img, alpha=float(self.contrast_var.get()), beta=int(self.brightness_var.get()))

        sat = float(self.saturation_var.get())
        if abs(sat - 1.0) > 0.001:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[:, :, 1] *= sat
            hsv[:, :, 1] = np.clip(hsv[:, :, 1], 0, 255)
            img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        gamma = float(self.gamma_var.get())
        if abs(gamma - 1.0) > 0.001:
            inv = 1.0 / gamma
            table = np.array([(i / 255.0) ** inv * 255 for i in range(256)], dtype=np.uint8)
            img = cv2.LUT(img, table)

        if self.denoise_var.get():
            img = cv2.bilateralFilter(img, 5, 35, 35)

        sharp = float(self.sharpness_var.get())
        if sharp > 0:
            blur = cv2.GaussianBlur(img, (0, 0), sigmaX=1.2)
            img = cv2.addWeighted(img, 1.0 + sharp, blur, -sharp, 0)

        z = max(0.5, float(self.zoom_var.get()))
        if z > 1.0:
            h, w = img.shape[:2]
            cw, ch = max(20, int(w / z)), max(20, int(h / z))
            cx = int(w / 2 - self.pan_x / z)
            cy = int(h / 2 - self.pan_y / z)
            x0 = max(0, min(w - cw, cx - cw // 2))
            y0 = max(0, min(h - ch, cy - ch // 2))
            img = img[y0:y0+ch, x0:x0+cw]

        if include_overlays:
            h, w = img.shape[:2]
            if self.grid_var.get():
                overlay = img.copy()
                for x in (w // 3, 2 * w // 3):
                    cv2.line(overlay, (x, 0), (x, h), (75, 160, 160), 1, cv2.LINE_AA)
                for y in (h // 3, 2 * h // 3):
                    cv2.line(overlay, (0, y), (w, y), (75, 160, 160), 1, cv2.LINE_AA)
                img = cv2.addWeighted(img, 0.88, overlay, 0.12, 0)
            if self.crosshair_var.get():
                cv2.line(img, (w//2-28, h//2), (w//2+28, h//2), (110, 235, 225), 1, cv2.LINE_AA)
                cv2.line(img, (w//2, h//2-28), (w//2, h//2+28), (110, 235, 225), 1, cv2.LINE_AA)
            if self.timestamp_var.get():
                cv2.rectangle(img, (10, h-35), (275, h-8), (3, 10, 12), -1)
                cv2.putText(img, datetime.now().strftime("%d %b %Y  %H:%M:%S"), (17, h-16), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (235, 250, 250), 1, cv2.LINE_AA)
        return img

    def _render_fit(self, frame):
        cw = max(300, self.video_canvas.winfo_width())
        ch = max(240, self.video_canvas.winfo_height())
        h, w = frame.shape[:2]
        scale = min(cw / w, ch / h)
        nw, nh = max(1, int(w*scale)), max(1, int(h*scale))
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        canvas = np.zeros((ch, cw, 3), np.uint8)
        x, y = (cw-nw)//2, (ch-nh)//2
        canvas[max(y,0):max(y,0)+nh, max(x,0):max(x,0)+nw] = resized
        return canvas

    def render(self, frame):
        processed = self.process_frame(frame, include_overlays=True)
        canvas = self._render_fit(processed)
        self.empty_title.place_forget()
        self.empty_sub.place_forget()
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        self.display_image = ImageTk.PhotoImage(image=image)
        self.video_canvas.delete("video")
        self.video_canvas.create_image(0, 0, image=self.display_image, anchor="nw", tags="video")
        self.viewer_meta.config(text=f"Zoom {self.zoom:.1f}×   •   {self.display_fps:.0f} fps")
        if self.recording:
            elapsed = int(time.time() - self.record_started)
            self.viewer_rec.config(text=f"● REC  {elapsed//60:02d}:{elapsed%60:02d}")
        else:
            self.viewer_rec.config(text="")

    def capture_snapshot(self):
        if self.last_frame is None:
            messagebox.showwarning("Snapshot", "No camera frame is available yet.", parent=self)
            return
        try:
            out_dir = Path(self.save_dir_var.get())
            out_dir.mkdir(parents=True, exist_ok=True)
            processed = self.process_frame(self.last_frame, True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = out_dir / f"CARE_ENT_Scope_{stamp}.jpg"
            if not cv2.imwrite(str(path), processed, [cv2.IMWRITE_JPEG_QUALITY, 97]):
                raise RuntimeError("JPEG save failed")
            self.status_label.config(text=f"Snapshot saved: {path.name}")
        except Exception as exc:
            messagebox.showerror("Snapshot", f"Could not save snapshot.\n\n{exc}", parent=self)

    def toggle_record(self):
        self.stop_recording() if self.recording else self.start_recording()

    def start_recording(self):
        if self.last_frame is None:
            messagebox.showwarning("Video Recording", "No camera frame is available yet.", parent=self)
            return
        try:
            VIDEO_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            frame = self.process_frame(self.last_frame, True)
            h, w = frame.shape[:2]
            fps = max(10.0, min(30.0, self.display_fps or 20.0))
            path = VIDEO_DIR / f"CARE_ENT_Scope_{stamp}.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
            if not writer.isOpened():
                writer.release()
                path = VIDEO_DIR / f"CARE_ENT_Scope_{stamp}.avi"
                writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
            if not writer.isOpened():
                raise RuntimeError("No compatible video encoder is available.")
            self.writer = writer
            self.recording = True
            self.record_path = path
            self.record_started = time.time()
            self.record_frames = 0
            self.last_record_id = -1
            self.record_button.set("■  Stop Recording", kind="danger")
            self.status_label.config(text=f"Recording started: {path.name}")
        except Exception as exc:
            messagebox.showerror("Video Recording", str(exc), parent=self)

    def stop_recording(self):
        if self.writer:
            try:
                self.writer.release()
            except Exception:
                pass
        path = self.record_path
        frames = self.record_frames
        self.writer = None
        self.recording = False
        self.record_path = None
        self.record_button.set("●  Start Recording", kind="secondary")
        if path:
            self.status_label.config(text=f"Recording saved: {path.name}  •  {frames} frames")

    def choose_save_folder(self):
        folder = filedialog.askdirectory(initialdir=self.save_dir_var.get(), title="Choose capture folder")
        if folder:
            self.save_dir_var.set(folder)
            Path(folder).mkdir(parents=True, exist_ok=True)
            self.status_label.config(text=f"Capture folder: {folder}")

    @staticmethod
    def open_folder(path):
        p = Path(path).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(p))
        except AttributeError:
            subprocess.Popen(["xdg-open", str(p)])

    def show_about(self):
        import webbrowser
        win = tk.Toplevel(self)
        win.title("About CARE ENT Scope Camera")
        win.configure(bg=BG)
        win.geometry("820x620")
        win.minsize(760, 560)
        win.transient(self)
        win.grab_set()
        win.update_idletasks()
        try:
            x = self.winfo_x() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
            y = self.winfo_y() + max(0, (self.winfo_height() - win.winfo_height()) // 2)
            win.geometry(f"{win.winfo_width()}x{win.winfo_height()}+{x}+{y}")
        except tk.TclError:
            pass

        hero = tk.Frame(win, bg=SURFACE, height=116)
        hero.pack(fill="x")
        hero.pack_propagate(False)

        logo = tk.Canvas(hero, width=70, height=70, bg=SURFACE, highlightthickness=0)
        logo.pack(side="left", padx=(24, 16), pady=22)
        logo.create_oval(4, 4, 66, 66, fill=TEAL_SOFT, outline=TEAL, width=2)
        logo.create_text(35, 35, text="C", fill=TEAL, font=("Arial", 27, "bold"))

        head = tk.Frame(hero, bg=SURFACE)
        head.pack(side="left", pady=18)
        tk.Label(head, text="CARE ENT Scope Camera", bg=SURFACE, fg=TEXT,
                 font=("Arial", 22, "bold")).pack(anchor="w")
        tk.Label(head, text=f"Professional Windows camera workstation   •   v{APP_VERSION}",
                 bg=SURFACE, fg="#78BBC1", font=("Arial", 9)).pack(anchor="w", pady=(4, 0))
        tk.Label(head, text="Developed by Dr. Abrar Khan  •  Care Hospital, Chikhli",
                 bg=SURFACE, fg=MUTED, font=("Arial", 9)).pack(anchor="w", pady=(5, 0))

        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(18, 8))
        body.grid_columnconfigure(0, minsize=180)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        nav = tk.Frame(body, bg=SURFACE_2, highlightthickness=1, highlightbackground=LINE)
        nav.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        tk.Label(nav, text="ABOUT", bg=SURFACE_2, fg=MUTED, font=("Arial", 8, "bold")).pack(anchor="w", padx=16, pady=(18, 8))

        content = tk.Frame(body, bg=SURFACE_2, highlightthickness=1, highlightbackground=LINE)
        content.grid(row=0, column=1, sticky="nsew")

        content_title = tk.Label(content, text="Overview", bg=SURFACE_2, fg=TEXT,
                                 font=("Arial", 17, "bold"))
        content_title.pack(anchor="w", padx=22, pady=(20, 4))
        content_sub = tk.Label(content, text="", bg=SURFACE_2, fg=MUTED, font=("Arial", 9))
        content_sub.pack(anchor="w", padx=22)
        content_text = tk.Label(content, text="", bg=SURFACE_2, fg="#C8DDE1", justify="left", anchor="nw",
                                wraplength=520, font=("Arial", 9), pady=16)
        content_text.pack(fill="both", expand=True, padx=22)
        action_row = tk.Frame(content, bg=SURFACE_2)
        action_row.pack(fill="x", padx=22, pady=(0, 18))

        pages = {
            "Overview": (
                "Purpose and capabilities",
                "Professional live viewing, capture and documentation workspace",
                "CARE ENT Scope Camera is a Windows companion viewer for the Wi-Fi ENT endoscope camera used in the CARE ENT Scope workflow.\n\n"
                "It receives the camera's local-network video stream and provides a focused workstation for live viewing, image capture and local video recording.\n\n"
                "Key capabilities\n"
                "• Live scope video and connection status\n"
                "• High-quality JPEG snapshots and local video recording\n"
                "• Zoom, pan, fit-to-view, mirror and rotation\n"
                "• Brightness, contrast, saturation, gamma and sharpness\n"
                "• Optional denoise, grid, crosshair and timestamp overlays\n"
                "• Freeze frame and full-screen viewing\n\n"
                "The viewer is designed as a practical camera workstation and does not require a cloud account for live display."
            ),
            "Privacy & Use": (
                "Local data and clinical use",
                "Patient media remains under the control of the Windows workstation",
                "Images and videos are saved to the local folder selected in the application. The camera viewer does not require cloud storage for live viewing.\n\n"
                "Clinical use notice\n"
                "This application is a camera viewing and documentation aid. It does not by itself establish a diagnosis, issue a prescription or replace professional clinical judgement.\n\n"
                "Users are responsible for appropriate patient consent, confidentiality and compliance with applicable privacy requirements when patient media is captured or stored."
            ),
            "Technical": (
                "Camera and software details",
                "Implementation information for support and deployment",
                "Camera transport\n"
                "Bebird-type Wi-Fi ENT scope stream received over UDP. Default endpoint: 192.168.10.123 : 8030.\n\n"
                "Media\n"
                "Snapshots are stored as high-quality JPEG files. Video is recorded locally using the available Windows/OpenCV video encoder with MP4 preferred and AVI/MJPEG fallback.\n\n"
                f"Application\nCARE ENT Scope Camera v{APP_VERSION} · Windows desktop application · local processing and local media storage.\n\n"
                "Support\n+91 9370111449   ·   www.carehospital.in"
            ),
            "Copyright": (
                "Intellectual property",
                "Ownership and software notice",
                "© 2026 Care Hospital. All rights reserved.\n\n"
                "CARE ENT Scope Camera is proprietary software developed by Dr. Abrar Khan at Care Hospital, Chikhli. The application interface, workflow and software implementation are part of the CARE ENT Scope software environment.\n\n"
                "This About page is provided for software identification, support and usage notice."
            ),
        }

        def set_page(name):
            title, subtitle, text = pages[name]
            content_title.config(text=name)
            content_sub.config(text=subtitle)
            content_text.config(text=text)
            for n, item in nav_items.items():
                item.configure(bg=TEAL_DARK if n == name else SURFACE_2,
                               fg=TEXT if n == name else "#A8C7CC")

        nav_items = {}
        for name in pages:
            lbl = tk.Label(nav, text=name, bg=SURFACE_2, fg="#A8C7CC", anchor="w",
                           font=("Arial", 9, "bold"), cursor="hand2")
            lbl.pack(fill="x", padx=10, pady=3, ipady=9)
            lbl.bind("<Button-1>", lambda _e, n=name: set_page(n))
            lbl.bind("<Enter>", lambda _e, w=lbl: w.configure(bg=TEAL_SOFT))
            lbl.bind("<Leave>", lambda _e, w=lbl, n=name: w.configure(bg=TEAL_DARK if n == content_title.cget("text") else SURFACE_2))
            nav_items[name] = lbl

        tk.Frame(nav, bg=SURFACE_2).pack(fill="both", expand=True)
        tk.Label(nav, text="CARE ENT", bg=SURFACE_2, fg=TEAL, font=("Arial", 9, "bold")).pack(anchor="w", padx=16)
        tk.Label(nav, text="Chikhli", bg=SURFACE_2, fg=MUTED, font=("Arial", 8)).pack(anchor="w", padx=16, pady=(2, 16))

        ModernButton(action_row, "Visit Website", lambda: webbrowser.open("https://www.carehospital.in"), width=136, height=36, icon="↗").pack(side="left")
        ModernButton(action_row, "Copy Support", lambda: self._copy_support(win), width=136, height=36, icon="▣").pack(side="left", padx=8)
        ModernButton(action_row, "Close", win.destroy, width=100, height=36, icon="×").pack(side="right")

        set_page("Overview")

    def _copy_support(self, parent):
        try:
            self.clipboard_clear()
            self.clipboard_append("+91 9370111449")
            self.update()
            self.status_label.config(text="Support number copied to clipboard")
        except tk.TclError:
            messagebox.showinfo("Support", "+91 9370111449", parent=parent)

    def ui_tick(self):
        if self.closed:
            return
        if self.stream:
            frame, frame_id = self.stream.get_frame()
            if frame is not None:
                if not self.freeze:
                    self.last_frame = frame
                if self.last_frame is not None:
                    self.render(self.last_frame)
                if self.recording and self.writer is not None and frame_id != self.last_record_id and not self.freeze:
                    try:
                        self.writer.write(self.process_frame(self.last_frame, True))
                        self.record_frames += 1
                        self.last_record_id = frame_id
                    except Exception as exc:
                        self.status_label.config(text=f"Recording error: {exc}")
                        self.stop_recording()

            now = time.time()
            if now - self.last_fps_time >= 1.0:
                self.display_fps = (self.stream.frame_count - self.last_frame_counter) / (now - self.last_fps_time)
                self.last_frame_counter = self.stream.frame_count
                self.last_fps_time = now
                age = now - self.stream.last_frame_time if self.stream.last_frame_time else 999
                if self.stream.last_frame_time and age < 1.5:
                    self.connection_badge.config(text="● LIVE", fg=GREEN)
                    self.viewer_meta.config(text=f"{self.stream.ip}:{self.stream.port}   •   {self.display_fps:.0f} fps   •   Zoom {self.zoom:.1f}×")
                    self.empty_title.place_forget()
                    self.empty_sub.place_forget()
                elif self.stream.running:
                    self.connection_badge.config(text="● WAITING", fg=AMBER)
                else:
                    self.connection_badge.config(text="● OFFLINE", fg="#C16C79")
                self.status_label.config(text=(
                    f"{self.stream.ip}:{self.stream.port}   •   {self.display_fps:.0f} fps   •   "
                    f"Packets {self.stream.pkt_count:,}   •   Frames {self.stream.frame_count:,}   •   Decoded {self.stream.decode_ok:,}"
                ))
        self.after(40, self.ui_tick)

    def on_close(self):
        self.closed = True
        if self.recording:
            self.stop_recording()
        if self.stream:
            self.stream.stop()
        self.destroy()


if __name__ == "__main__":
    CameraApp().mainloop()
