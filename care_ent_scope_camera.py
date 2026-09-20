"""
CARE ENT Scope Camera - Professional Windows camera workstation
================================================================

Wi-Fi ENT endoscope (Bebird-type) live viewer, capture and recording workstation.

Developed by Dr. Abrar Khan - Care Hospital, Chikhli
(c) 2026 Care Hospital. All rights reserved.

--------------------------------------------------------------------------
VERSION 3.0.0 - what changed from 2.0.0
--------------------------------------------------------------------------
FIX 1  Buttons no longer flicker on hover.
       The old buttons were a Frame with a Label inside it. Moving the mouse
       from the Frame onto its child Label fires <Leave> then <Enter> over and
       over, so the button repainted continuously. Every button is now a single
       Canvas widget with no child widgets, so there is nothing to cross into
       and hover is one clean colour change (see class RoundButton).

FIX 2  The "Connect Camera" button disappears once video starts.
       The welcome overlay is now bound to one rule: it is on screen only while
       there is no picture (self.last_frame is None). The first decoded frame
       hides it, disconnecting brings it back. See _sync_overlay().

FIX 3  Full Screen shows only the video, not the whole application.
       F11 / the Full Screen button now opens a separate black window that
       contains nothing but the video canvas, and the render loop draws into
       that window while it is open. Esc, F11 or a double-click closes it.
       See open_video_fullscreen() / close_video_fullscreen().

FIX 4  Professional, elegant look with rounded buttons.
       Rounded pill buttons, rounded panels, rounded status badges, a refined
       clinical palette and Segoe UI typography throughout.
--------------------------------------------------------------------------
"""

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
from tkinter import filedialog, messagebox, font as tkfont

from io import BytesIO
from PIL import Image, ImageDraw, ImageEnhance, ImageFile, ImageFilter, ImageFont, ImageTk

# Frames arrive over UDP, so a packet is occasionally lost and the JPEG for
# that frame is incomplete. Decoding what did arrive keeps the picture live
# instead of dropping the frame outright.
ImageFile.LOAD_TRUNCATED_IMAGES = True

# ==========================================================================
# APPLICATION CONSTANTS
# ==========================================================================

APP_NAME = "CARE ENT Scope Camera"
APP_VERSION = "3.0.0"
APP_VENDOR = "Care Hospital, Chikhli"
APP_AUTHOR = "Dr. Abrar Khan"
APP_PHONE = "+91 9370111449"
APP_WEB = "www.carehospital.in"

# --- Wi-Fi scope stream protocol (Bebird-type UDP/JPEG) -------------------
CAMERA_IP_DEFAULT = "192.168.10.123"
CAMERA_PORT_DEFAULT = 8030
HDR = 51            # bytes of packet header before the JPEG payload
STRIDE = 1388       # payload bytes per chunk index
TYPE_VIDEO = 3      # packet type id for video payload
KEEPALIVE_PERIOD = 0.4
RECV_BUFFER = 4 * 1024 * 1024

# --- Local storage --------------------------------------------------------
home = Path.home()
BASE_DIR = home / "Documents" / "CARE ENT Scope"
CAPTURE_DIR = BASE_DIR / "Captures"
VIDEO_DIR = BASE_DIR / "Videos"
for folder in (BASE_DIR, CAPTURE_DIR, VIDEO_DIR):
    folder.mkdir(parents=True, exist_ok=True)

# ==========================================================================
# PALETTE  (refined clinical dark theme)
# ==========================================================================

BG = "#081216"          # window background
SURFACE = "#0E1F26"     # panels / cards
SURFACE_2 = "#142C35"   # inputs, raised surfaces
SURFACE_3 = "#1B3C47"   # hover surfaces
LINE = "#23464F"        # hairline borders
TEXT = "#EDF8FA"        # primary text
MUTED = "#89A9B1"       # secondary text
DIM = "#5E7E85"         # tertiary text
TEAL = "#2FC7B7"        # accent
TEAL_DARK = "#17837C"   # accent pressed
TEAL_SOFT = "#0D3136"   # accent tinted surface
RED = "#E2596A"
RED_DARK = "#8E2E3C"
AMBER = "#E3B457"
GREEN = "#4FD3A4"
BLACK = "#03080A"

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"


def font(size=10, weight="normal"):
    """Return a font tuple, preferring Segoe UI on Windows."""
    return (FONT_FAMILY, size, weight)


# ==========================================================================
# STREAM RECEIVER  (unchanged protocol - verified against the original build)
# ==========================================================================

def start_packet():
    """24-byte START command: magic 0x9999 (LE u16) at 0, opcode 1 at byte 2."""
    b = bytearray(24)
    struct.pack_into("<H", b, 0, 0x9999)
    b[2] = 1
    return bytes(b)


def stop_packet():
    """24-byte STOP command: magic 0x9999 (LE u16) at 0, opcode 2 at byte 2."""
    b = bytearray(24)
    struct.pack_into("<H", b, 0, 0x9999)
    b[2] = 2
    return bytes(b)


START = start_packet()
STOP = stop_packet()


class MjpegAviWriter:
    """Writes Motion-JPEG video into an AVI container.

    The camera already hands us JPEG frames and every capture is re-encoded as
    JPEG anyway, so recording is just a matter of wrapping those frames in an
    AVI. Doing it here rather than through OpenCV's VideoWriter keeps the whole
    OpenCV/NumPy stack out of the application - the program is a fraction of
    the size and starts much faster - and MJPEG AVI plays in Windows Media
    Player, VLC, PowerPoint and every common editor.

    Sizes that are only known once recording stops (total frames, chunk sizes)
    are written as placeholders and patched in close().
    """

    def __init__(self, path, width, height, fps, quality=90):
        self.path = Path(path)
        self.width = int(width)
        self.height = int(height)
        self.fps = max(int(fps), 1)
        self.quality = quality
        self.frames = 0
        self._index = []                 # (offset from 'movi', payload size)
        self._file = open(self.path, "wb")
        self._write_headers()

    # -- little-endian helpers ----------------------------------------
    @staticmethod
    def _u32(value):
        return struct.pack("<I", value & 0xFFFFFFFF)

    @staticmethod
    def _u16(value):
        return struct.pack("<H", value & 0xFFFF)

    def _write_headers(self):
        f = self._file
        w, h, fps = self.width, self.height, self.fps

        # --- main AVI header (56 bytes) ---
        avih = b"".join((
            self._u32(1000000 // fps),   # microseconds per frame
            self._u32(0),                # max bytes per second
            self._u32(0),                # padding granularity
            self._u32(0x10),             # AVIF_HASINDEX
            self._u32(0),                # total frames - patched later
            self._u32(0),                # initial frames
            self._u32(1),                # streams
            self._u32(w * h * 3),        # suggested buffer size
            self._u32(w), self._u32(h),
            self._u32(0), self._u32(0), self._u32(0), self._u32(0),
        ))

        # --- stream header (56 bytes) ---
        strh = b"".join((
            b"vids", b"MJPG",
            self._u32(0), self._u16(0), self._u16(0),
            self._u32(0),                # initial frames
            self._u32(1),                # scale
            self._u32(fps),              # rate -> fps = rate/scale
            self._u32(0),                # start
            self._u32(0),                # length - patched later
            self._u32(w * h * 3),        # suggested buffer size
            self._u32(0xFFFFFFFF),       # quality: default
            self._u32(0),                # sample size
            self._u16(0), self._u16(0), self._u16(w), self._u16(h),
        ))

        # --- bitmap info header (40 bytes) ---
        strf = b"".join((
            self._u32(40),
            self._u32(w), self._u32(h),
            self._u16(1), self._u16(24),
            b"MJPG",
            self._u32(w * h * 3),
            self._u32(0), self._u32(0), self._u32(0), self._u32(0),
        ))

        strl = b"strh" + self._u32(len(strh)) + strh + \
               b"strf" + self._u32(len(strf)) + strf
        hdrl = b"avih" + self._u32(len(avih)) + avih + \
               b"LIST" + self._u32(len(strl) + 4) + b"strl" + strl

        f.write(b"RIFF")
        self._riff_size_pos = f.tell()
        f.write(self._u32(0))            # RIFF size - patched later
        f.write(b"AVI ")
        f.write(b"LIST" + self._u32(len(hdrl) + 4) + b"hdrl" + hdrl)

        # Remember where the patchable fields live.
        hdrl_start = self._riff_size_pos + 4 + 4 + 8 + 4   # -> 'avih' fourcc
        self._avih_frames_pos = hdrl_start + 8 + 16
        self._strh_length_pos = hdrl_start + 8 + 56 + 12 + 8 + 32

        f.write(b"LIST")
        self._movi_size_pos = f.tell()
        f.write(self._u32(0))            # 'movi' size - patched later
        self._movi_pos = f.tell()        # position of the 'movi' fourcc
        f.write(b"movi")

    def write(self, image):
        """Append one frame. `image` is a Pillow image."""
        # Every frame in an AVI must be the same size. Rotating or flipping
        # mid-recording changes the frame dimensions, so fit it back to the
        # size the file was opened with rather than corrupting the stream.
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.BILINEAR)
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=self.quality)
        payload = buffer.getvalue()

        f = self._file
        offset = f.tell() - self._movi_pos
        f.write(b"00dc" + self._u32(len(payload)) + payload)
        if len(payload) & 1:
            f.write(b"\x00")             # chunks are word-aligned
        self._index.append((offset, len(payload)))
        self.frames += 1

    def close(self):
        if self._file is None:
            return
        f = self._file
        movi_end = f.tell()

        # --- index ---
        entries = bytearray()
        for offset, size in self._index:
            entries += b"00dc" + self._u32(0x10) + self._u32(offset) + self._u32(size)
        f.write(b"idx1" + self._u32(len(entries)) + bytes(entries))
        file_end = f.tell()

        # --- patch the sizes we could not know up front ---
        f.seek(self._movi_size_pos)
        f.write(self._u32(movi_end - self._movi_pos))
        f.seek(self._avih_frames_pos)
        f.write(self._u32(self.frames))
        f.seek(self._strh_length_pos)
        f.write(self._u32(self.frames))
        f.seek(self._riff_size_pos)
        f.write(self._u32(file_end - self._riff_size_pos - 4))

        f.close()
        self._file = None


class ScopeStream:
    """Bebird-type UDP/JPEG stream receiver.

    The scope sends each JPEG frame as a series of UDP packets. Every packet
    carries a 51-byte header; byte offset 2 holds the packet type and offset 33
    holds the 1-based chunk index. Chunk 1 begins with the JPEG SOI marker
    (FF D8); each chunk occupies STRIDE bytes at (index - 1) * STRIDE in the
    reassembled frame buffer.
    """

    def __init__(self, ip, port):
        self.ip = ip
        self.port = int(port)
        self.sock = None
        self.running = False
        self.latest = None
        self.latest_id = 0
        self.lock = threading.Lock()
        self.last_frame_time = 0.0
        self.pkt_count = 0
        self.frame_count = 0
        self.decode_ok = 0
        self.last_len = 0
        self.error_text = ""

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self.running:
            return
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFFER)
            except OSError:
                pass
            self.sock.bind(("", 0))
            self.sock.settimeout(1.0)
        except OSError as exc:
            self.error_text = str(exc)
            return
        self.running = True
        threading.Thread(target=self._keepalive, daemon=True).start()
        threading.Thread(target=self._recv, daemon=True).start()

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.sendto(STOP, (self.ip, self.port))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    # -- worker threads ----------------------------------------------------
    def _keepalive(self):
        while self.running:
            if self.sock:
                try:
                    self.sock.sendto(START, (self.ip, self.port))
                except OSError as exc:
                    self.error_text = str(exc)
            time.sleep(KEEPALIVE_PERIOD)

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
            img = Image.open(BytesIO(bytes(buf)))
            img.load()
            if img.mode != "RGB":
                img = img.convert("RGB")
        except Exception:
            return
        self.decode_ok += 1
        with self.lock:
            self.latest = img
            self.latest_id += 1
            self.last_frame_time = time.time()

    def get_frame(self):
        with self.lock:
            if self.latest is None:
                return None, 0
            return self.latest.copy(), self.latest_id


# ==========================================================================
# ROUNDED UI PRIMITIVES
# ==========================================================================

def rounded_points(x1, y1, x2, y2, r):
    """Point list for a smooth-splined rounded rectangle."""
    r = max(0.0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [
        x1 + r, y1,
        x2 - r, y1, x2, y1,
        x2, y1 + r,
        x2, y2 - r, x2, y2,
        x2 - r, y2,
        x1 + r, y2, x1, y2,
        x1, y2 - r,
        x1, y1 + r, x1, y1,
    ]


class RoundButton(tk.Canvas):
    """A rounded pill button drawn on a single Canvas.

    FIX 1 - NO HOVER FLICKER.
    The previous implementation nested a Label inside a Frame. Tk delivers a
    <Leave> to the Frame the moment the pointer crosses onto the child Label,
    immediately followed by an <Enter>, which made the button repaint in a
    loop and read as flicker. This widget has no children at all: the shape
    and the caption are Canvas *items*, which never generate crossing events.
    Hover therefore fires exactly once and only recolours existing items -
    no widget is created, resized or re-packed.
    """

    STYLES = {
        # kind:       (fill,      hover,      active,     text,   border)
        "primary":   (TEAL,       "#45D8C8",  TEAL_DARK,  "#04191B", ""),
        "secondary": (SURFACE_2,  SURFACE_3,  "#0E242B",  TEXT,     LINE),
        "ghost":     ("",         SURFACE_2,  SURFACE_3,  MUTED,    ""),
        "danger":    (RED,        "#EC6D7D",  RED_DARK,   "#1A0508", ""),
        "success":   (GREEN,      "#68E0B6",  "#2C8F70",  "#03110C", ""),
    }

    def __init__(self, parent, text="", command=None, kind="secondary",
                 width=None, height=38, radius=None, font_size=10,
                 font_weight="bold", padding=22, bg=None, **kw):
        self._parent_bg = bg or parent.cget("background")
        super().__init__(parent, height=height, bd=0, highlightthickness=0,
                         bg=self._parent_bg, takefocus=0, **kw)

        self._text = text
        self._command = command
        self._kind = kind if kind in self.STYLES else "secondary"
        self._height = height
        self._radius = radius if radius is not None else height / 2.0
        self._font = font(font_size, font_weight)
        self._padding = padding
        self._enabled = True
        self._state = "normal"          # normal | hover | active
        self._shape = None
        self._caption = None

        if width is None:
            measure = tkfont.Font(font=self._font)
            width = measure.measure(text) + padding * 2
        self.configure(width=max(int(width), height))

        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.configure(cursor="hand2")

    # -- painting ----------------------------------------------------------
    def _colors(self):
        fill, hover, active, fg, border = self.STYLES[self._kind]
        if not self._enabled:
            return SURFACE_2, DIM, LINE
        if self._state == "active":
            return (active or fill), fg, border
        if self._state == "hover":
            return (hover or fill), fg, border
        return fill, fg, border

    def _redraw(self, _event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        fill, fg, border = self._colors()
        pts = rounded_points(1, 1, w - 1, h - 1, self._radius)
        self._shape = self.create_polygon(
            pts, smooth=True, splinesteps=24,
            fill=(fill if fill else self._parent_bg),
            outline=(border if border else (fill if fill else self._parent_bg)),
            width=1,
        )
        self._caption = self.create_text(
            w / 2, h / 2 + 1, text=self._text, fill=fg,
            font=self._font, anchor="center",
        )

    def _repaint(self):
        """Recolour existing items only - cheapest possible hover update."""
        if self._shape is None:
            self._redraw()
            return
        fill, fg, border = self._colors()
        self.itemconfigure(
            self._shape,
            fill=(fill if fill else self._parent_bg),
            outline=(border if border else (fill if fill else self._parent_bg)),
        )
        self.itemconfigure(self._caption, fill=fg)

    # -- events ------------------------------------------------------------
    def _on_enter(self, _e=None):
        if not self._enabled or self._state == "hover":
            return
        self._state = "hover"
        self._repaint()

    def _on_leave(self, _e=None):
        if self._state == "normal":
            return
        self._state = "normal"
        self._repaint()

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._state = "active"
        self._repaint()

    def _on_release(self, event=None):
        if not self._enabled:
            return
        inside = (
            event is not None
            and 0 <= event.x <= self.winfo_width()
            and 0 <= event.y <= self.winfo_height()
        )
        self._state = "hover" if inside else "normal"
        self._repaint()
        if inside and callable(self._command):
            self._command()

    # -- public API --------------------------------------------------------
    def set_text(self, text, autosize=True):
        if text == self._text:
            return
        self._text = text
        if autosize:
            measure = tkfont.Font(font=self._font)
            self.configure(width=max(
                measure.measure(text) + self._padding * 2, self._height))
        if self._caption is not None:
            self.itemconfigure(self._caption, text=text)
        else:
            self._redraw()

    def set_kind(self, kind):
        if kind not in self.STYLES or kind == self._kind:
            return
        self._kind = kind
        self._repaint()

    def set_enabled(self, enabled):
        if enabled == self._enabled:
            return
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._state = "normal"
        self._repaint()

    def set_command(self, command):
        self._command = command


class RoundedPanel(tk.Frame):
    """A container with rounded corners.

    A Canvas paints the rounded card; `panel.body` is an ordinary Frame placed
    inside it and is what callers put widgets into.
    """

    def __init__(self, parent, radius=16, fill=SURFACE, border=LINE,
                 pad=1, bg=None, autosize=False, **kw):
        outer_bg = bg or parent.cget("background")
        super().__init__(parent, bg=outer_bg, bd=0, highlightthickness=0, **kw)
        self._radius = radius
        self._fill = fill
        self._border = border
        self._outer_bg = outer_bg
        self._pad = pad
        self._autosize = autosize

        self._canvas = tk.Canvas(self, bd=0, highlightthickness=0, bg=outer_bg)
        self._canvas.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.body = tk.Frame(self, bg=fill, bd=0, highlightthickness=0)
        self.body.place(relx=0, rely=0, relwidth=1, relheight=1,
                        x=pad, y=pad, width=-2 * pad, height=-2 * pad)

        self.bind("<Configure>", self._redraw, add="+")

        # `body` is placed, not packed, so this frame has no natural size of its
        # own. Panels that must hug their content (the command deck) ask for
        # autosize and take their height from the body's requested height.
        if autosize:
            self.body.bind("<Configure>", self._autofit, add="+")
            self.after_idle(self._autofit)

    def _autofit(self, _event=None):
        if not self._autosize:
            return
        wanted = self.body.winfo_reqheight() + 2 * self._pad
        if wanted > 2 and abs(wanted - self.winfo_height()) > 1:
            self.configure(height=wanted)

    def _redraw(self, _event=None):
        self._canvas.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self._canvas.create_polygon(
            rounded_points(0.5, 0.5, w - 0.5, h - 0.5, self._radius),
            smooth=True, splinesteps=24,
            fill=self._fill, outline=self._border, width=1,
        )


class StatusBadge(tk.Canvas):
    """Rounded status pill with a coloured indicator dot."""

    def __init__(self, parent, text="OFFLINE", color=MUTED, width=136,
                 height=30, bg=None, **kw):
        self._bg = bg or parent.cget("background")
        super().__init__(parent, width=width, height=height, bd=0,
                         highlightthickness=0, bg=self._bg, takefocus=0, **kw)
        self._text = text
        self._color = color
        self.bind("<Configure>", self._redraw, add="+")

    def _redraw(self, _e=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.create_polygon(
            rounded_points(1, 1, w - 1, h - 1, (h - 2) / 2.0),
            smooth=True, splinesteps=20,
            fill=SURFACE_2, outline=LINE, width=1,
        )
        cy = h / 2
        self.create_oval(14, cy - 4, 22, cy + 4, fill=self._color, outline="")
        self.create_text(30, cy + 1, text=self._text, fill=self._color,
                         font=font(9, "bold"), anchor="w")

    def set(self, text, color):
        if text == self._text and color == self._color:
            return
        self._text = text
        self._color = color
        self._redraw()


class ThinScrollbar(tk.Canvas):
    """A slim rounded scrollbar drawn entirely by us.

    Tk's stock scrollbar is painted in the platform's own style, which shows up
    as a pale grey bar against this dark interface on Windows. Drawing it here
    keeps the panels looking the same everywhere.

    It is a drop-in for tk.Scrollbar: pass `command=widget.xview` (or yview)
    and register it with `widget.configure(xscrollcommand=bar.set)`.
    """

    def __init__(self, parent, orient="vertical", command=None,
                 thickness=10, bg=None, **kw):
        self._bg = bg or parent.cget("background")
        horizontal = orient.startswith("h")
        super().__init__(
            parent, bd=0, highlightthickness=0, bg=self._bg, takefocus=0,
            **({"height": thickness} if horizontal else {"width": thickness}), **kw
        )
        self._horizontal = horizontal
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._hover = False

        self.bind("<Configure>", lambda _e: self._draw(), add="+")
        self.bind("<ButtonPress-1>", self._on_drag, add="+")
        self.bind("<B1-Motion>", self._on_drag, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")

    # -- scrolled widget calls this -----------------------------------
    def set(self, first, last):
        self._first = max(0.0, min(1.0, float(first)))
        self._last = max(0.0, min(1.0, float(last)))
        self._draw()

    def _on_enter(self, _e=None):
        self._hover = True
        self._draw()

    def _on_leave(self, _e=None):
        self._hover = False
        self._draw()

    def _on_drag(self, event):
        if not callable(self._command):
            return
        length = self.winfo_width() if self._horizontal else self.winfo_height()
        if length <= 1:
            return
        pos = event.x if self._horizontal else event.y
        span = max(self._last - self._first, 0.0)
        fraction = (pos / length) - span / 2.0
        self._command("moveto", max(0.0, min(1.0 - span, fraction)))

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        # Nothing to scroll - draw nothing at all.
        if self._first <= 0.0 and self._last >= 1.0:
            return
        radius = (h if self._horizontal else w) / 2.0
        self.create_polygon(
            rounded_points(0, 0, w, h, radius), smooth=True, splinesteps=12,
            fill=SURFACE_2, outline="",
        )
        colour = TEAL if self._hover else SURFACE_3
        if self._horizontal:
            x1 = self._first * w
            x2 = max(self._last * w, x1 + 18)
            self.create_polygon(
                rounded_points(x1, 1, min(x2, w), h - 1, radius),
                smooth=True, splinesteps=12, fill=colour, outline="",
            )
        else:
            y1 = self._first * h
            y2 = max(self._last * h, y1 + 18)
            self.create_polygon(
                rounded_points(1, y1, w - 1, min(y2, h), radius),
                smooth=True, splinesteps=12, fill=colour, outline="",
            )


# ==========================================================================
# MAIN APPLICATION
# ==========================================================================

class CameraApp:

    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME}  -  v{APP_VERSION}")
        self.root.configure(bg=BG)
        self.root.minsize(1180, 720)
        self._start_window()

        # -- stream / frame state -----------------------------------------
        self.stream = None
        self.last_frame = None
        self.display_image = None
        self.fs_image = None
        self.closed = False

        # -- view state ----------------------------------------------------
        self.zoom = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self.drag_start = None
        self.freeze = False

        # -- recording state ------------------------------------------------
        self.recording = False
        self.writer = None
        self.record_path = None
        self.record_started = 0.0
        self.record_frames = 0
        self.last_record_id = -1

        # -- statistics -----------------------------------------------------
        self.display_fps = 0.0
        self.frames_since = 0
        self.last_frame_counter = -1
        self.last_fps_time = time.time()

        # -- fullscreen video window (FIX 3) --------------------------------
        self.fs_window = None
        self.fs_canvas = None

        # -- overlay visibility bookkeeping (FIX 2) -------------------------
        self._overlay_shown = None      # None = never laid out yet

        # -- tk variables ----------------------------------------------------
        self.camera_ip = tk.StringVar(value=CAMERA_IP_DEFAULT)
        self.camera_port = tk.StringVar(value=str(CAMERA_PORT_DEFAULT))
        self.auto_start = tk.BooleanVar(value=True)
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.brightness_var = tk.IntVar(value=0)
        self.contrast_var = tk.IntVar(value=0)
        self.saturation_var = tk.IntVar(value=100)
        self.gamma_var = tk.IntVar(value=100)
        self.sharpness_var = tk.IntVar(value=0)
        self.denoise_var = tk.IntVar(value=0)
        self.flip_h = tk.BooleanVar(value=False)
        self.flip_v = tk.BooleanVar(value=False)
        self.rotate_var = tk.StringVar(value="0")
        self.grid_var = tk.BooleanVar(value=False)
        self.crosshair_var = tk.BooleanVar(value=False)
        self.timestamp_var = tk.BooleanVar(value=False)
        self.chrome_var = tk.BooleanVar(value=True)
        self.save_dir_var = tk.StringVar(value=str(CAPTURE_DIR))
        self.drawer_visible = False

        self._build_styles()
        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Escape>", self._escape)
        self.root.bind("<F11>", lambda _e: self.toggle_video_fullscreen())
        self.root.bind("<space>", lambda _e: self.toggle_freeze())
        self.root.bind("<Control-s>", lambda _e: self.capture_snapshot())
        self.root.bind("<Control-r>", lambda _e: self.toggle_record())
        self.root.bind("<plus>", lambda _e: self.zoom_step(0.2))
        self.root.bind("<equal>", lambda _e: self.zoom_step(0.2))
        self.root.bind("<minus>", lambda _e: self.zoom_step(-0.2))

        self.root.after(80, self.ui_tick)
        if self.auto_start.get():
            self.root.after(300, self.connect_camera)

    # ------------------------------------------------------------------
    # WINDOW / STYLE SETUP
    # ------------------------------------------------------------------
    def _start_window(self):
        try:
            self.root.state("zoomed")
        except tk.TclError:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.root.geometry(f"{min(sw, 1600)}x{min(sh - 60, 940)}+40+20")

    def _build_styles(self):
        self.root.option_add("*Font", font(10))
        self.root.option_add("*Entry.background", SURFACE_2)
        self.root.option_add("*Entry.foreground", TEXT)
        self.root.option_add("*Entry.insertBackground", TEAL)

    def _label(self, parent, text, size=10, color=TEXT, weight="normal",
               bg=None, anchor="w", **kw):
        return tk.Label(parent, text=text, font=font(size, weight),
                        fg=color, bg=bg or parent.cget("background"),
                        anchor=anchor, **kw)

    # ------------------------------------------------------------------
    # LAYOUT
    # ------------------------------------------------------------------
    def _build_ui(self):
        # ---------------- header ----------------
        header = tk.Frame(self.root, bg=BG, height=76)
        header.pack(side="top", fill="x", padx=18, pady=(14, 8))
        header.pack_propagate(False)

        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left", fill="y")

        logo = tk.Canvas(brand, width=46, height=46, bd=0, highlightthickness=0, bg=BG)
        logo.pack(side="left", padx=(0, 14))
        logo.create_polygon(rounded_points(1, 1, 45, 45, 14), smooth=True,
                            splinesteps=20, fill=TEAL_SOFT, outline=TEAL, width=1)
        logo.create_text(23, 24, text="C", fill=TEAL, font=font(20, "bold"))

        text_box = tk.Frame(brand, bg=BG)
        text_box.pack(side="left", fill="y")
        self._label(text_box, APP_NAME, size=15, weight="bold", bg=BG).pack(anchor="w")
        self._label(text_box, "PROFESSIONAL CAMERA WORKSTATION", size=8,
                    color="#6FA8B0", bg=BG).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=BG)
        right.pack(side="right", fill="y")

        self.settings_button = RoundButton(right, text="Controls", kind="secondary",
                                           command=self.toggle_drawer, bg=BG)
        self.settings_button.pack(side="right", padx=(8, 0), pady=8)

        self.fullscreen_button = RoundButton(right, text="Full Screen", kind="secondary",
                                             command=self.toggle_video_fullscreen, bg=BG)
        self.fullscreen_button.pack(side="right", padx=8, pady=8)

        RoundButton(right, text="About", kind="ghost",
                    command=self.show_about, bg=BG).pack(side="right", padx=8, pady=8)

        self.connection_badge = StatusBadge(right, text="OFFLINE", color=MUTED, bg=BG)
        self.connection_badge.pack(side="right", padx=(8, 12), pady=8)

        # ---------------- footer ----------------
        # Packed before the viewer so the expanding viewer cannot squeeze the
        # fixed-height chrome out of the layout.
        footer = tk.Frame(self.root, bg=BG, height=34)
        footer.pack(side="bottom", fill="x", padx=18, pady=(0, 12))
        footer.pack_propagate(False)
        self.status_label = self._label(footer, "Ready", size=9, color=MUTED, bg=BG)
        self.status_label.pack(side="left")
        self._label(footer,
                    "Ctrl+S Snapshot     Ctrl+R Record     Space Freeze     F11 Video full screen",
                    size=9, color=DIM, bg=BG, anchor="e").pack(side="right")

        # ---------------- command deck ----------------
        self._build_command_deck()

        # ---------------- main viewer ----------------
        main = tk.Frame(self.root, bg=BG)
        main.pack(side="top", fill="both", expand=True, padx=18)

        self.viewer_card = RoundedPanel(main, radius=18, fill=BLACK,
                                        border=LINE, bg=BG)
        self.viewer_card.pack(fill="both", expand=True)

        viewer = self.viewer_card.body
        self.video_canvas = tk.Canvas(viewer, bg=BLACK, bd=0, highlightthickness=0,
                                      cursor="crosshair")
        self.video_canvas.pack(fill="both", expand=True)
        self.video_canvas.bind("<ButtonPress-1>", self.on_pan_start)
        self.video_canvas.bind("<B1-Motion>", self.on_pan_move)
        self.video_canvas.bind("<ButtonRelease-1>", self.on_pan_end)
        self.video_canvas.bind("<MouseWheel>", self.on_mouse_zoom)
        self.video_canvas.bind("<Button-4>", lambda e: self.zoom_step(0.15))
        self.video_canvas.bind("<Button-5>", lambda e: self.zoom_step(-0.15))
        self.video_canvas.bind("<Double-Button-1>",
                               lambda _e: self.toggle_video_fullscreen())

        # viewer chrome (floating labels over the video)
        self.viewer_title = self._label(viewer, "LIVE VIEW", size=9, weight="bold",
                                        color=TEAL, bg=BLACK)
        self.viewer_title.place(x=18, y=14)

        self.viewer_meta = self._label(viewer, "No signal", size=9, color=MUTED,
                                       bg=BLACK, anchor="e")
        self.viewer_meta.place(relx=1.0, x=-18, y=14, anchor="ne")

        self.viewer_rec = self._label(viewer, "", size=9, weight="bold",
                                      color=RED, bg=BLACK)
        self.viewer_rec.place(x=18, y=36)

        # ---------- welcome / empty-state overlay (FIX 2) ----------
        self.empty_overlay = tk.Frame(viewer, bg=BLACK)
        self._label(self.empty_overlay, "Camera ready", size=17, weight="bold",
                    bg=BLACK, anchor="center").pack()
        self._label(self.empty_overlay,
                    "Connect to the Wi-Fi scope to start live viewing",
                    size=10, color=MUTED, bg=BLACK, anchor="center").pack(pady=(8, 20))
        self.overlay_button = RoundButton(self.empty_overlay, text="Connect Camera",
                                          kind="primary", command=self.connect_camera,
                                          height=44, font_size=11, padding=30, bg=BLACK)
        self.overlay_button.pack()
        self._sync_overlay(force=True)

        # ---------------- controls drawer ----------------
        self.drawer = RoundedPanel(self.root, radius=18, fill=SURFACE,
                                   border=LINE, bg=BG)
        self.drawer_built = False

    def _build_command_deck(self):
        deck_wrap = tk.Frame(self.root, bg=BG)
        deck_wrap.pack(side="bottom", fill="x", padx=18, pady=(12, 0))

        deck_card = RoundedPanel(deck_wrap, radius=16, fill=SURFACE, border=LINE,
                                 bg=BG, autosize=True)
        deck_card.pack(fill="x")
        deck = deck_card.body

        # The command bar is one scrollable row. On a narrow window or a
        # smaller screen the sections would otherwise be cut off at the right
        # edge, so the row scrolls sideways and the scrollbar appears only
        # when the buttons actually overflow.
        holder = tk.Frame(deck, bg=SURFACE)
        holder.pack(fill="x", padx=16, pady=(12, 10))

        canvas = tk.Canvas(holder, bg=SURFACE, bd=0, highlightthickness=0)
        canvas.pack(side="top", fill="x")

        hbar = ThinScrollbar(holder, orient="horizontal", command=canvas.xview,
                             thickness=10, bg=SURFACE)
        canvas.configure(xscrollcommand=hbar.set)

        inner = tk.Frame(canvas, bg=SURFACE)
        canvas.create_window((0, 0), window=inner, anchor="nw")

        def _deck_resize(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            wanted = inner.winfo_reqheight()
            if wanted > 1 and abs(canvas.winfo_height() - wanted) > 1:
                canvas.configure(height=wanted)
            overflowing = inner.winfo_reqwidth() > canvas.winfo_width() + 1
            if overflowing and not hbar.winfo_ismapped():
                hbar.pack(side="top", fill="x", pady=(8, 0))
            elif not overflowing and hbar.winfo_ismapped():
                hbar.pack_forget()
                canvas.xview_moveto(0)

        inner.bind("<Configure>", _deck_resize, add="+")
        canvas.bind("<Configure>", _deck_resize, add="+")

        def _deck_wheel(event):
            if inner.winfo_reqwidth() <= canvas.winfo_width():
                return
            step = -1 if getattr(event, "delta", 0) > 0 or event.num == 4 else 1
            canvas.xview_scroll(step, "units")

        for seq in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind(seq, _deck_wheel, add="+")

        # -- CAPTURE ------------------------------------------------------
        cap = tk.Frame(inner, bg=SURFACE)
        cap.pack(side="left", fill="y")
        self._section_title(cap, "CAPTURE")
        cap_row = tk.Frame(cap, bg=SURFACE)
        cap_row.pack(anchor="w", pady=(8, 0))

        self.snapshot_button = RoundButton(cap_row, text="Snapshot", kind="primary",
                                           command=self.capture_snapshot, bg=SURFACE)
        self.snapshot_button.pack(side="left", padx=(0, 8))

        self.record_button = RoundButton(cap_row, text="Start Recording", kind="secondary",
                                         command=self.toggle_record, bg=SURFACE)
        self.record_button.pack(side="left")

        self._deck_divider(inner)

        # -- VIEW ---------------------------------------------------------
        view = tk.Frame(inner, bg=SURFACE)
        view.pack(side="left", fill="y")
        self._section_title(view, "VIEW")
        view_row = tk.Frame(view, bg=SURFACE)
        view_row.pack(anchor="w", pady=(8, 0))

        for label, cmd in (
            ("Fit", lambda: self.set_zoom(1.0)),
            ("Zoom -", lambda: self.zoom_step(-0.2)),
            ("Zoom +", lambda: self.zoom_step(0.2)),
            ("Mirror", self.toggle_mirror),
            ("Rotate", self.rotate_once),
        ):
            RoundButton(view_row, text=label, kind="secondary", command=cmd,
                        bg=SURFACE).pack(side="left", padx=(0, 8))

        self.freeze_button = RoundButton(view_row, text="Freeze", kind="secondary",
                                         command=self.toggle_freeze, bg=SURFACE)
        self.freeze_button.pack(side="left")

        self._deck_divider(inner)

        # -- SESSION ------------------------------------------------------
        sess = tk.Frame(inner, bg=SURFACE)
        sess.pack(side="left", fill="y")
        self._section_title(sess, "SESSION")
        sess_row = tk.Frame(sess, bg=SURFACE)
        sess_row.pack(anchor="w", pady=(8, 0))

        RoundButton(sess_row, text="Captures", kind="secondary",
                    command=lambda: self.open_folder(self.save_dir_var.get()),
                    bg=SURFACE).pack(side="left", padx=(0, 8))
        RoundButton(sess_row, text="Videos", kind="secondary",
                    command=lambda: self.open_folder(str(VIDEO_DIR)),
                    bg=SURFACE).pack(side="left")

        self._deck_divider(inner)

        # -- CONNECTION ----------------------------------------------------
        conn = tk.Frame(inner, bg=SURFACE)
        conn.pack(side="left", fill="y")
        self._section_title(conn, "CONNECTION")
        conn_row = tk.Frame(conn, bg=SURFACE)
        conn_row.pack(anchor="w", pady=(8, 0))
        self.deck_connect_button = RoundButton(conn_row, text="Connect",
                                               kind="primary",
                                               command=self.toggle_connection,
                                               bg=SURFACE)
        self.deck_connect_button.pack(side="left")

    def _deck_divider(self, parent):
        tk.Frame(parent, bg=LINE, width=1).pack(side="left", fill="y", padx=16, pady=4)

    def _section_title(self, parent, text, anchor="w"):
        self._label(parent, text.upper(), size=8, weight="bold",
                    color=DIM, anchor=anchor).pack(anchor=anchor)

    # ------------------------------------------------------------------
    # CONTROLS DRAWER
    # ------------------------------------------------------------------
    def toggle_drawer(self):
        if self.drawer_visible:
            self.drawer.place_forget()
            self.drawer_visible = False
            self.settings_button.set_text("Controls")
            return
        if not self.drawer_built:
            self._populate_drawer()
            self.drawer_built = True
        self.drawer.place(relx=1.0, rely=0, x=-18, y=96, anchor="ne",
                          width=410, relheight=0.70)
        self.drawer.lift()
        self.drawer_visible = True
        self.settings_button.set_text("Close Controls")

    def _populate_drawer(self):
        body = self.drawer.body
        head = tk.Frame(body, bg=SURFACE)
        head.pack(fill="x", padx=18, pady=(16, 8))
        self._label(head, "CONTROL CENTER", size=11, weight="bold", bg=SURFACE).pack(anchor="w")
        self._label(head, "Camera connection, image and overlays", size=9,
                    color=MUTED, bg=SURFACE).pack(anchor="w", pady=(2, 0))

        # ---- scrollable region: vertical scrollbar on the side plus a
        # ---- horizontal scrollbar, so nothing in the panel is ever clipped.
        wrap = tk.Frame(body, bg=SURFACE)
        wrap.pack(fill="both", expand=True, padx=(10, 6), pady=(4, 12))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(wrap, bg=SURFACE, bd=0, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")

        vbar = ThinScrollbar(wrap, orient="vertical", command=canvas.yview,
                             thickness=10, bg=SURFACE)
        vbar.grid(row=0, column=1, sticky="ns", padx=(5, 0))
        hbar = ThinScrollbar(wrap, orient="horizontal", command=canvas.xview,
                             thickness=10, bg=SURFACE)
        hbar.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        inner = tk.Frame(canvas, bg=SURFACE)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _resize(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            # Stretch the content to the panel width, but never below its own
            # natural width - that surplus is what the horizontal bar scrolls.
            canvas.itemconfigure(
                window, width=max(canvas.winfo_width(), inner.winfo_reqwidth()))

        inner.bind("<Configure>", _resize, add="+")
        canvas.bind("<Configure>", _resize, add="+")

        # ---- mouse wheel: plain wheel scrolls vertically, Shift+wheel
        # ---- scrolls horizontally, only while the pointer is over the panel.
        def _wheel(event):
            step = -1 if getattr(event, "delta", 0) > 0 or event.num == 4 else 1
            canvas.yview_scroll(step, "units")

        def _shift_wheel(event):
            step = -1 if getattr(event, "delta", 0) > 0 or event.num == 4 else 1
            canvas.xview_scroll(step, "units")

        def _bind_wheel(_e=None):
            canvas.bind_all("<MouseWheel>", _wheel)
            canvas.bind_all("<Shift-MouseWheel>", _shift_wheel)
            canvas.bind_all("<Button-4>", _wheel)
            canvas.bind_all("<Button-5>", _wheel)

        def _unbind_wheel(_e=None):
            for seq in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
                canvas.unbind_all(seq)

        for widget in (canvas, inner):
            widget.bind("<Enter>", _bind_wheel, add="+")
            widget.bind("<Leave>", _unbind_wheel, add="+")
        self.drawer.bind("<Unmap>", _unbind_wheel, add="+")

        # ---- Connection ----
        self._drawer_section(inner, "Connection", "Wi-Fi scope endpoint")
        self._entry(inner, "Camera IP", self.camera_ip)
        self._entry(inner, "UDP Port", self.camera_port)
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x", padx=10, pady=(10, 4))
        self.drawer_connect_button = RoundButton(row, text="Connect", kind="primary",
                                                 command=self.connect_camera, bg=SURFACE)
        self.drawer_connect_button.pack(side="left", padx=(0, 8))
        RoundButton(row, text="Disconnect", kind="secondary",
                    command=self.disconnect_camera, bg=SURFACE).pack(side="left")
        self._check(inner, "Auto-connect on launch", self.auto_start)

        # ---- Image ----
        self._drawer_section(inner, "Image",
                             "Software processing - camera hardware exposure is unchanged")
        self._slider(inner, "Brightness", self.brightness_var, -100, 100)
        self._slider(inner, "Contrast", self.contrast_var, -100, 100)
        self._slider(inner, "Saturation", self.saturation_var, 0, 200)
        self._slider(inner, "Gamma", self.gamma_var, 50, 200)
        self._slider(inner, "Sharpness", self.sharpness_var, 0, 100)
        self._slider(inner, "Soft denoise", self.denoise_var, 0, 100)

        # ---- Orientation ----
        self._drawer_section(inner, "Orientation", "Preview and capture transforms")
        self._check(inner, "Mirror horizontally", self.flip_h)
        self._check(inner, "Flip vertically", self.flip_v)
        rot = tk.Frame(inner, bg=SURFACE)
        rot.pack(fill="x", padx=10, pady=(6, 4))
        self._label(rot, "Rotation", size=9, color=MUTED, bg=SURFACE).pack(side="left")
        opts = tk.OptionMenu(rot, self.rotate_var, "0", "90", "180", "270")
        opts.configure(bg=SURFACE_2, fg=TEXT, activebackground=SURFACE_3,
                       activeforeground=TEXT, bd=0, highlightthickness=0,
                       font=font(9), width=6)
        opts["menu"].configure(bg=SURFACE_2, fg=TEXT, bd=0,
                               activebackground=TEAL_DARK, font=font(9))
        opts.pack(side="right")

        # ---- Overlays ----
        self._drawer_section(inner, "Overlays",
                             "Visual aids are included in display and capture")
        self._check(inner, "Composition grid", self.grid_var)
        self._check(inner, "Crosshair", self.crosshair_var)
        self._check(inner, "Timestamp", self.timestamp_var)
        self._check(inner, "Viewer chrome", self.chrome_var)

        # ---- Session ----
        self._drawer_section(inner, "Session", "Local workstation storage")
        self._label(inner, self.save_dir_var.get(), size=8, color=DIM, bg=SURFACE,
                    wraplength=320, justify="left").pack(anchor="w", padx=10, pady=(0, 8))
        row2 = tk.Frame(inner, bg=SURFACE)
        row2.pack(fill="x", padx=10, pady=(0, 6))
        RoundButton(row2, text="Choose Capture Folder", kind="secondary",
                    command=self.choose_save_folder, bg=SURFACE).pack(side="left")
        RoundButton(inner, text="Reset All Controls", kind="ghost",
                    command=self.reset_image, bg=SURFACE).pack(anchor="w", padx=10, pady=(8, 16))

    def _drawer_section(self, parent, title, subtitle):
        tk.Frame(parent, bg=LINE, height=1).pack(fill="x", padx=10, pady=(16, 12))
        self._label(parent, title, size=10, weight="bold",
                    bg=SURFACE).pack(anchor="w", padx=10)
        self._label(parent, subtitle, size=8, color=DIM, bg=SURFACE,
                    wraplength=330, justify="left").pack(anchor="w", padx=10, pady=(2, 8))

    def _entry(self, parent, label, variable):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", padx=10, pady=4)
        self._label(row, label, size=9, color=MUTED, bg=SURFACE, width=10).pack(side="left")
        holder = tk.Frame(row, bg=SURFACE_2, highlightthickness=1,
                          highlightbackground=LINE, highlightcolor=TEAL)
        holder.pack(side="right", fill="x", expand=True)
        tk.Entry(holder, textvariable=variable, bg=SURFACE_2, fg=TEXT, bd=0,
                 relief="flat", insertbackground=TEAL, font=font(10)).pack(
            fill="x", padx=8, ipady=5)

    def _slider(self, parent, label, variable, lo, hi):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", padx=10, pady=(6, 0))
        self._label(row, label, size=9, color="#B5CDD2", bg=SURFACE).pack(side="left")
        value = self._label(row, str(variable.get()), size=9, color=TEAL,
                            bg=SURFACE, anchor="e")
        value.pack(side="right")
        scale = tk.Scale(parent, variable=variable, from_=lo, to=hi,
                         orient="horizontal", showvalue=False, resolution=1,
                         bg=SURFACE, fg=TEXT, troughcolor="#16343C",
                         activebackground=TEAL, highlightthickness=0, bd=0,
                         sliderrelief="flat", sliderlength=18, width=8,
                         command=lambda _v: value.configure(text=str(variable.get())))
        scale.pack(fill="x", padx=10)

    def _check(self, parent, label, variable):
        tk.Checkbutton(parent, text=label, variable=variable, bg=SURFACE, fg=TEXT,
                       selectcolor=SURFACE_2, activebackground=SURFACE,
                       activeforeground=TEAL, bd=0, highlightthickness=0,
                       font=font(9), anchor="w").pack(fill="x", padx=8, pady=2)

    # ------------------------------------------------------------------
    # CONNECTION
    # ------------------------------------------------------------------
    def toggle_connection(self):
        if self.stream and self.stream.running:
            self.disconnect_camera()
        else:
            self.connect_camera()

    def connect_camera(self):
        ip = self.camera_ip.get().strip()
        try:
            socket.inet_aton(ip)
            port = int(self.camera_port.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except (OSError, ValueError):
            messagebox.showerror("Camera Connection",
                                 "Please enter a valid IPv4 address and UDP port.")
            return

        self.disconnect_camera(quiet=True)
        self.stream = ScopeStream(ip, port)
        self.stream.start()
        self.connection_badge.set("CONNECTING", AMBER)
        self.viewer_title.configure(text="WAITING", fg=AMBER)
        self.status_label.configure(text=f"Listening for {ip}:{port}")
        self._set_overlay_message(
            "Waiting for camera",
            "Make sure the PC is connected to the scope Wi-Fi network")
        self._sync_overlay()
        self._sync_connect_buttons()

    def disconnect_camera(self, quiet=False):
        if self.recording:
            self.stop_recording()
        if self.stream:
            self.stream.stop()
        self.stream = None
        self.last_frame = None          # FIX 2: brings the overlay back
        self.display_image = None
        self.video_canvas.delete("all")
        self.connection_badge.set("OFFLINE", MUTED)
        self.viewer_title.configure(text="LIVE VIEW", fg=TEAL)
        self.viewer_meta.configure(text="No signal")
        self._set_overlay_message(
            "Camera ready",
            "Connect to the Wi-Fi scope to start live viewing")
        self._sync_overlay()
        self._sync_connect_buttons()
        if not quiet:
            self.status_label.configure(text="Camera disconnected")

    def _sync_connect_buttons(self):
        live = bool(self.stream and self.stream.running)
        self.deck_connect_button.set_text("Disconnect" if live else "Connect")
        self.deck_connect_button.set_kind("secondary" if live else "primary")

    # ------------------------------------------------------------------
    # FIX 2 - EMPTY-STATE OVERLAY VISIBILITY
    # ------------------------------------------------------------------
    def _set_overlay_message(self, title, subtitle):
        children = self.empty_overlay.winfo_children()
        if len(children) >= 2:
            children[0].configure(text=title)
            children[1].configure(text=subtitle)

    def _sync_overlay(self, force=False):
        """The welcome panel (and its Connect Camera button) is visible only
        while there is no picture. One decoded frame hides it; disconnecting
        or losing the stream brings it back. Calls are idempotent - place() is
        only invoked when the state actually changes, which also removes a
        second source of flicker."""
        should_show = self.last_frame is None

        # Lay out (or remove) only on a real change - repeated place() calls
        # every tick would themselves cause flicker.
        if force or should_show != self._overlay_shown:
            self._overlay_shown = should_show
            if should_show:
                self.empty_overlay.place(relx=0.5, rely=0.5, anchor="center")
                self.empty_overlay.lift()
            else:
                self.empty_overlay.place_forget()

        # While the panel is on screen keep its button in step with the stream.
        # set_text / set_enabled are no-ops when nothing changed.
        if should_show:
            connecting = bool(self.stream and self.stream.running)
            self.overlay_button.set_text("Connecting..." if connecting
                                         else "Connect Camera")
            self.overlay_button.set_enabled(not connecting)

    # ------------------------------------------------------------------
    # FIX 3 - VIDEO-ONLY FULL SCREEN
    # ------------------------------------------------------------------
    def toggle_video_fullscreen(self):
        if self.fs_window is not None:
            self.close_video_fullscreen()
        else:
            self.open_video_fullscreen()

    def open_video_fullscreen(self):
        """Open a separate window that contains the video and nothing else.

        The old behaviour set -fullscreen on the root window, so the header,
        command deck and footer went full screen along with the picture. Here
        a dedicated Toplevel holds a single black Canvas; the render loop
        detects it and draws frames into it instead of the docked viewer.
        """
        if self.fs_window is not None:
            return
        win = tk.Toplevel(self.root)
        win.configure(bg=BLACK)
        win.title(f"{APP_NAME} - Video")
        # Set an explicit screen-sized geometry as well as the fullscreen
        # attribute, so the video window still covers the display if the
        # window manager ignores -fullscreen.
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{sw}x{sh}+0+0")
        try:
            win.attributes("-fullscreen", True)
        except tk.TclError:
            win.overrideredirect(True)

        canvas = tk.Canvas(win, bg=BLACK, bd=0, highlightthickness=0,
                           cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.bind("<MouseWheel>", self.on_mouse_zoom)
        canvas.bind("<Button-4>", lambda e: self.zoom_step(0.15))
        canvas.bind("<Button-5>", lambda e: self.zoom_step(-0.15))
        canvas.bind("<ButtonPress-1>", self.on_pan_start)
        canvas.bind("<B1-Motion>", self.on_pan_move)
        canvas.bind("<ButtonRelease-1>", self.on_pan_end)
        canvas.bind("<Double-Button-1>", lambda _e: self.close_video_fullscreen())

        win.bind("<Escape>", lambda _e: self.close_video_fullscreen())
        win.bind("<F11>", lambda _e: self.close_video_fullscreen())
        win.protocol("WM_DELETE_WINDOW", self.close_video_fullscreen)

        hint = tk.Label(win, text="Esc or F11 to exit full screen",
                        bg=BLACK, fg=DIM, font=font(9))
        hint.place(relx=0.5, rely=1.0, y=-26, anchor="s")
        win.after(2600, lambda: hint.destroy() if hint.winfo_exists() else None)

        self.fs_window = win
        self.fs_canvas = canvas
        self.fullscreen_button.set_text("Exit Full Screen")
        win.lift()
        win.focus_force()

    def close_video_fullscreen(self):
        if self.fs_window is None:
            return
        try:
            self.fs_window.destroy()
        except tk.TclError:
            pass
        self.fs_window = None
        self.fs_canvas = None
        self.fs_image = None
        self.fullscreen_button.set_text("Full Screen")

    def _escape(self, _event=None):
        if self.fs_window is not None:
            self.close_video_fullscreen()
        elif self.drawer_visible:
            self.toggle_drawer()

    # ------------------------------------------------------------------
    # VIEW CONTROLS
    # ------------------------------------------------------------------
    def toggle_freeze(self):
        self.freeze = not self.freeze
        if self.freeze:
            self.viewer_title.configure(text="FROZEN FRAME", fg=AMBER)
            self.freeze_button.set_text("Resume")
            self.freeze_button.set_kind("primary")
            self.status_label.configure(text="Display frozen")
        else:
            self.viewer_title.configure(text="LIVE VIEW", fg=TEAL)
            self.freeze_button.set_text("Freeze")
            self.freeze_button.set_kind("secondary")
            self.status_label.configure(text="Live display resumed")

    def set_zoom(self, value):
        self.zoom = max(1.0, min(float(value), 6.0))
        self.zoom_var.set(self.zoom)
        if abs(self.zoom - 1.0) < 1e-6:
            self.pan_x = self.pan_y = 0

    def zoom_step(self, delta):
        self.set_zoom(round(self.zoom + delta, 2))

    def on_mouse_zoom(self, event):
        self.zoom_step(0.15 if event.delta > 0 else -0.15)

    def on_pan_start(self, event):
        self.drag_start = (event.x, event.y, self.pan_x, self.pan_y)

    def on_pan_move(self, event):
        if not self.drag_start:
            return
        x0, y0, px, py = self.drag_start
        self.pan_x = px + (event.x - x0)
        self.pan_y = py + (event.y - y0)

    def on_pan_end(self, _event=None):
        self.drag_start = None

    def toggle_mirror(self):
        self.flip_h.set(not self.flip_h.get())

    def rotate_once(self):
        vals = ["0", "90", "180", "270"]
        self.rotate_var.set(vals[(vals.index(self.rotate_var.get()) + 1) % 4])

    def reset_image(self):
        self.brightness_var.set(0)
        self.contrast_var.set(0)
        self.saturation_var.set(100)
        self.gamma_var.set(100)
        self.sharpness_var.set(0)
        self.denoise_var.set(0)
        self.flip_h.set(False)
        self.flip_v.set(False)
        self.rotate_var.set("0")
        self.grid_var.set(False)
        self.crosshair_var.set(False)
        self.timestamp_var.set(False)
        self.set_zoom(1.0)
        self.pan_x = self.pan_y = 0
        self.status_label.configure(text="Image and view controls reset")

    # ------------------------------------------------------------------
    # IMAGE PIPELINE
    # ------------------------------------------------------------------
    _GAMMA_CACHE = {}

    @staticmethod
    def _gamma_table(gamma_percent):
        """256-entry lookup table for a gamma value, built once per setting."""
        table = CameraApp._GAMMA_CACHE.get(gamma_percent)
        if table is None:
            g = max(gamma_percent, 1) / 100.0
            single = [min(255, int(((i / 255.0) ** (1.0 / g)) * 255 + 0.5))
                      for i in range(256)]
            table = single * 3          # one band each for R, G and B
            CameraApp._GAMMA_CACHE[gamma_percent] = table
        return table

    def _overlay_font(self, size):
        cached = getattr(self, "_font_cache", None)
        if cached is None:
            cached = self._font_cache = {}
        font_obj = cached.get(size)
        if font_obj is None:
            for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
                try:
                    font_obj = ImageFont.truetype(name, size)
                    break
                except OSError:
                    continue
            if font_obj is None:
                font_obj = ImageFont.load_default()
            cached[size] = font_obj
        return font_obj

    def process_frame(self, frame, include_overlays=True):
        """Apply every transform, adjustment and overlay to one frame.

        Works on Pillow images throughout. The controls map directly onto
        Pillow's enhancement operations, which keeps the application small and
        quick to start - the whole OpenCV/NumPy stack is no longer needed.
        """
        if frame is None:
            return None
        out = frame

        if self.flip_h.get():
            out = out.transpose(Image.FLIP_LEFT_RIGHT)
        if self.flip_v.get():
            out = out.transpose(Image.FLIP_TOP_BOTTOM)

        rot = self.rotate_var.get()
        if rot == "90":
            out = out.transpose(Image.ROTATE_270)      # clockwise
        elif rot == "180":
            out = out.transpose(Image.ROTATE_180)
        elif rot == "270":
            out = out.transpose(Image.ROTATE_90)       # anticlockwise

        # From here on we own the pixels, so operations may work in place.
        if out is frame:
            out = out.copy()

        contrast = self.contrast_var.get()
        if contrast:
            out = ImageEnhance.Contrast(out).enhance(1.0 + contrast / 100.0)

        brightness = self.brightness_var.get()
        if brightness:
            out = ImageEnhance.Brightness(out).enhance(1.0 + brightness / 100.0)

        sat = self.saturation_var.get()
        if sat != 100:
            out = ImageEnhance.Color(out).enhance(sat / 100.0)

        gamma = self.gamma_var.get()
        if gamma != 100:
            out = out.point(self._gamma_table(gamma))

        denoise = self.denoise_var.get()
        if denoise > 0:
            out = out.filter(ImageFilter.GaussianBlur(radius=denoise / 70.0))

        sharp = self.sharpness_var.get()
        if sharp > 0:
            out = ImageEnhance.Sharpness(out).enhance(1.0 + sharp / 40.0)

        if include_overlays and (self.grid_var.get() or self.crosshair_var.get()
                                 or self.timestamp_var.get()):
            w, h = out.size
            draw = ImageDraw.Draw(out)
            colour = (200, 230, 235)
            if self.grid_var.get():
                for i in (1, 2):
                    draw.line([(w * i // 3, 0), (w * i // 3, h)], fill=colour, width=1)
                    draw.line([(0, h * i // 3), (w, h * i // 3)], fill=colour, width=1)
            if self.crosshair_var.get():
                cx, cy = w // 2, h // 2
                draw.line([(cx - 26, cy), (cx + 26, cy)], fill=colour, width=1)
                draw.line([(cx, cy - 26), (cx, cy + 26)], fill=colour, width=1)
                draw.rectangle([cx - 26, cy - 26, cx + 26, cy + 26],
                               outline=colour, width=1)
            if self.timestamp_var.get():
                stamp = datetime.now().strftime("%d %b %Y  %H:%M:%S")
                font_obj = self._overlay_font(max(14, int(h * 0.04)))
                draw.text((14, h - 14), stamp, font=font_obj, anchor="ls",
                          fill=(255, 255, 255),
                          stroke_width=2, stroke_fill=(0, 0, 0))
        return out

    def _render_fit(self, frame, cw, ch):
        """Scale `frame` into a cw x ch letterboxed canvas, honouring zoom/pan."""
        w, h = frame.size
        if w == 0 or h == 0 or cw <= 1 or ch <= 1:
            return None
        scale = min(cw / w, ch / h) * self.zoom
        nw, nh = max(int(w * scale), 1), max(int(h * scale), 1)
        resample = Image.BILINEAR if scale >= 1 else Image.LANCZOS
        resized = frame.resize((nw, nh), resample)

        ox = (cw - nw) // 2 + int(self.pan_x)
        oy = (ch - nh) // 2 + int(self.pan_y)

        # Crop away anything that falls outside the viewport before pasting,
        # so panning never tries to write past the canvas edges.
        sx0, sy0 = max(0, -ox), max(0, -oy)
        vis_w = min(nw - sx0, cw - max(0, ox))
        vis_h = min(nh - sy0, ch - max(0, oy))

        canvas = Image.new("RGB", (cw, ch), (3, 8, 10))
        if vis_w > 0 and vis_h > 0:
            piece = resized.crop((sx0, sy0, sx0 + vis_w, sy0 + vis_h))
            canvas.paste(piece, (max(0, ox), max(0, oy)))
        return canvas

    def render(self):
        """Draw the current frame into whichever canvas is active."""
        fullscreen = self.fs_canvas is not None
        canvas = self.fs_canvas if fullscreen else self.video_canvas

        self._sync_overlay()
        if self.last_frame is None:
            return

        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return

        processed = self.process_frame(self.last_frame, include_overlays=True)
        if processed is None:
            return

        # feed the recorder with the processed (overlay-burned) frame
        if self.recording and self.writer is not None:
            try:
                self.writer.write(processed)
                self.record_frames += 1
            except Exception as exc:
                self.status_label.configure(text=f"Recording error: {exc}")

        fitted = self._render_fit(processed, cw, ch)
        if fitted is None:
            return
        photo = ImageTk.PhotoImage(fitted)

        canvas.delete("video")
        canvas.create_image(0, 0, image=photo, anchor="nw", tags="video")
        if fullscreen:
            self.fs_image = photo          # keep a reference alive
        else:
            self.display_image = photo

    # ------------------------------------------------------------------
    # CAPTURE
    # ------------------------------------------------------------------
    def capture_snapshot(self):
        if self.last_frame is None:
            messagebox.showwarning(APP_NAME, "No camera frame is available yet.")
            return
        frame = self.process_frame(self.last_frame, include_overlays=True)
        out_dir = Path(self.save_dir_var.get())
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = out_dir / f"CARE_ENT_Scope_{stamp}.jpg"
            frame.save(str(path), "JPEG", quality=95, subsampling=0)
            self.status_label.configure(text=f"Snapshot saved: {path.name}")
        except Exception:
            messagebox.showwarning(APP_NAME, "Could not save snapshot.")

    def toggle_record(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self.last_frame is None:
            messagebox.showwarning("Video Recording", "No camera frame is available yet.")
            return
        frame = self.process_frame(self.last_frame, include_overlays=True)
        w, h = frame.size
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fps = int(round(max(min(self.display_fps, 30.0), 10.0)))

        path = VIDEO_DIR / f"CARE_ENT_Scope_{stamp}.avi"
        try:
            VIDEO_DIR.mkdir(parents=True, exist_ok=True)
            self.writer = MjpegAviWriter(path, w, h, fps)
            self.record_path = path
        except Exception as exc:
            self.writer = None
            messagebox.showwarning("Video Recording",
                                   f"Could not start recording.\n\n{exc}")
            return

        self.recording = True
        self.record_started = time.time()
        self.record_frames = 0
        self.record_button.set_text("Stop Recording")
        self.record_button.set_kind("danger")
        self.status_label.configure(text=f"Recording started: {self.record_path.name}")

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        if self.writer is not None:
            try:
                self.writer.close()
            except Exception:
                pass
        self.writer = None
        self.record_button.set_text("Start Recording")
        self.record_button.set_kind("secondary")
        self.viewer_rec.configure(text="")
        if self.record_path:
            self.status_label.configure(
                text=f"Recording saved: {self.record_path.name} - {self.record_frames} frames")

    def choose_save_folder(self):
        folder = filedialog.askdirectory(title="Choose capture folder",
                                         initialdir=self.save_dir_var.get())
        if folder:
            self.save_dir_var.set(folder)
            self.status_label.configure(text=f"Capture folder: {folder}")

    def open_folder(self, path):
        path = os.path.expanduser(str(path))
        try:
            os.startfile(path)                       # Windows
        except AttributeError:
            subprocess.Popen(["xdg-open", path])     # other platforms
        except Exception:
            self.status_label.configure(text="Could not open the folder.")

    # ------------------------------------------------------------------
    # MAIN LOOP
    # ------------------------------------------------------------------
    def ui_tick(self):
        if self.closed:
            return
        try:
            if self.stream:
                frame, fid = self.stream.get_frame()
                if frame is not None and not self.freeze:
                    self.last_frame = frame
                    if fid != self.last_frame_counter:
                        self.frames_since += 1
                        self.last_frame_counter = fid

            now = time.time()
            if now - self.last_fps_time >= 0.5:
                self.display_fps = self.frames_since / (now - self.last_fps_time)
                self.frames_since = 0
                self.last_fps_time = now

            self.render()
            self._update_chrome(now)
        except tk.TclError:
            return
        except Exception:
            pass
        self.root.after(30, self.ui_tick)

    def _update_chrome(self, now):
        live = bool(self.stream and self.stream.running)
        has_signal = live and self.last_frame is not None and \
            (now - self.stream.last_frame_time) < 2.5

        if not live:
            self.connection_badge.set("OFFLINE", MUTED)
        elif has_signal:
            self.connection_badge.set("LIVE", GREEN)
        else:
            self.connection_badge.set("CONNECTING", AMBER)

        show_chrome = self.chrome_var.get()
        for widget in (self.viewer_title, self.viewer_meta, self.viewer_rec):
            if show_chrome:
                widget.lift()
            else:
                widget.lower()

        if not self.freeze and live:
            self.viewer_title.configure(
                text="LIVE VIEW" if has_signal else "WAITING",
                fg=TEAL if has_signal else AMBER)

        if self.stream:
            self.viewer_meta.configure(
                text=(f"{self.display_fps:.0f} fps    Zoom {self.zoom:.1f}x    "
                      f"Packets {self.stream.pkt_count}    "
                      f"Frames {self.stream.frame_count}    "
                      f"Decoded {self.stream.decode_ok}"))

        if self.recording:
            elapsed = int(now - self.record_started)
            self.viewer_rec.configure(
                text=f"REC  {elapsed // 60:02d}:{elapsed % 60:02d}")

    def on_close(self):
        self.closed = True
        self.close_video_fullscreen()
        if self.recording:
            self.stop_recording()
        if self.stream:
            self.stream.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # ABOUT
    # ------------------------------------------------------------------
    def show_about(self):
        win = tk.Toplevel(self.root)
        win.title(f"About {APP_NAME}")
        win.configure(bg=BG)
        win.geometry("860x640")
        win.transient(self.root)
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_x() + 120}+{self.root.winfo_y() + 70}")

        hero = tk.Frame(win, bg=BG)
        hero.pack(fill="x", padx=28, pady=(24, 12))
        self._label(hero, APP_NAME, size=18, weight="bold", bg=BG).pack(anchor="w")
        self._label(hero, f"Professional Windows camera workstation   -   v{APP_VERSION}",
                    size=10, color="#78BBC1", bg=BG).pack(anchor="w", pady=(4, 0))
        self._label(hero, f"Developed by {APP_AUTHOR}  -  {APP_VENDOR}",
                    size=9, color=MUTED, bg=BG).pack(anchor="w", pady=(6, 0))

        card = RoundedPanel(win, radius=16, fill=SURFACE, border=LINE, bg=BG)
        card.pack(fill="both", expand=True, padx=28, pady=(6, 12))
        body = card.body

        pages = {
            "Overview": (
                "Purpose and capabilities",
                "CARE ENT Scope Camera is a Windows companion viewer for the Wi-Fi ENT "
                "endoscope camera used in the CARE ENT Scope workflow. It receives the "
                "camera's local-network video stream and provides a focused workstation "
                "for live viewing, image capture and local video recording.\n\n"
                "Key capabilities\n"
                "  -  Live scope video and connection status\n"
                "  -  High-quality JPEG snapshots and local video recording\n"
                "  -  Zoom, pan, fit-to-view, mirror and rotation\n"
                "  -  Brightness, contrast, saturation, gamma and sharpness\n"
                "  -  Optional denoise, grid, crosshair and timestamp overlays\n"
                "  -  Freeze frame and video-only full-screen viewing\n\n"
                "The viewer is designed as a practical camera workstation and does not "
                "require a cloud account for live display."),
            "Privacy & Use": (
                "Local data and clinical use",
                "Patient media remains under the control of the Windows workstation. "
                "Images and videos are saved to the local folder selected in the "
                "application. The camera viewer does not require cloud storage for live "
                "viewing.\n\n"
                "Clinical use notice\n"
                "This application is a camera viewing and documentation aid. It does not "
                "by itself establish a diagnosis, issue a prescription or replace "
                "professional clinical judgement.\n\n"
                "Users are responsible for appropriate patient consent, confidentiality "
                "and compliance with applicable privacy requirements when patient media "
                "is captured or stored."),
            "Technical": (
                "Camera and software details",
                f"Camera transport\n"
                f"Bebird-type Wi-Fi ENT scope stream received over UDP. Default endpoint: "
                f"{CAMERA_IP_DEFAULT} : {CAMERA_PORT_DEFAULT}.\n\n"
                "Media\n"
                "Snapshots are stored as high-quality JPEG files. Video is recorded "
                "locally using the available Windows/OpenCV video encoder with MP4 "
                "preferred and AVI/MJPEG fallback.\n\n"
                "Application\n"
                f"{APP_NAME} v{APP_VERSION} - Windows desktop application with local "
                "processing and local media storage.\n\n"
                "Support\n"
                f"{APP_PHONE}   -   {APP_WEB}"),
            "Copyright": (
                "Ownership and software notice",
                "(c) 2026 Care Hospital. All rights reserved.\n\n"
                f"{APP_NAME} is proprietary software developed by {APP_AUTHOR} at "
                f"{APP_VENDOR}. The application interface, workflow and software "
                "implementation are part of the CARE ENT Scope software environment.\n\n"
                "This About page is provided for software identification, support and "
                "usage notice."),
        }

        nav = tk.Frame(body, bg=SURFACE)
        nav.pack(side="left", fill="y", padx=16, pady=16)
        content = tk.Frame(body, bg=SURFACE)
        content.pack(side="left", fill="both", expand=True, padx=(4, 18), pady=16)

        content_title = self._label(content, "", size=12, weight="bold", bg=SURFACE)
        content_title.pack(anchor="w")
        content_text = self._label(content, "", size=9, color="#C8DDE1", bg=SURFACE,
                                   justify="left", wraplength=520)
        content_text.pack(anchor="w", pady=(10, 0))

        nav_buttons = {}

        def set_page(name):
            subtitle, text = pages[name]
            content_title.configure(text=subtitle)
            content_text.configure(text=text)
            for key, btn in nav_buttons.items():
                btn.set_kind("primary" if key == name else "ghost")

        for name in pages:
            btn = RoundButton(nav, text=name, kind="ghost", width=150,
                              command=lambda n=name: set_page(n), bg=SURFACE)
            btn.pack(anchor="w", pady=3)
            nav_buttons[name] = btn
        set_page("Overview")

        actions = tk.Frame(win, bg=BG)
        actions.pack(fill="x", padx=28, pady=(0, 20))
        RoundButton(actions, text="Visit Website", kind="secondary",
                    command=lambda: __import__("webbrowser").open(f"https://{APP_WEB}"),
                    bg=BG).pack(side="left", padx=(0, 8))
        RoundButton(actions, text="Copy Support Number", kind="secondary",
                    command=self._copy_support, bg=BG).pack(side="left")
        RoundButton(actions, text="Close", kind="primary",
                    command=win.destroy, bg=BG).pack(side="right")

    def _copy_support(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(APP_PHONE)
        messagebox.showinfo("Support", "Support number copied to clipboard")


# ==========================================================================
def main():
    root = tk.Tk()
    CameraApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
