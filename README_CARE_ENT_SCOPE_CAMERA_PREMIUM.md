# CARE ENT Scope Camera — Windows Premium Edition

Version 2.0.0

A polished Windows desktop companion viewer for the Wi-Fi ENT endoscope camera used in the CARE ENT Scope workflow.

## Core camera protocol

The application preserves the verified camera transport from the supplied viewer:
- Camera IP default: `192.168.10.123`
- UDP port: `8030`
- 51-byte packet header
- Video packet type: `3`
- JPEG payload chunks: 1388-byte stride

## Interface

The application is designed to fit standard 1366×768 and larger Windows displays and opens maximized when possible. The permanent sidebar has been replaced by a clean central live-view workspace with a compact command deck. Detailed controls live in an on-demand **Control Center** drawer.

## Controls

- Connect / Disconnect
- Auto-connect on launch
- Snapshot capture
- Local video recording (MP4 preferred; AVI/MJPEG fallback)
- Fit-to-view
- Zoom 0.5×–4× with mouse-wheel control
- Pan while zoomed
- Horizontal mirror
- Vertical flip
- 90° / 180° / 270° rotation
- Brightness
- Contrast
- Saturation
- Gamma
- Sharpness
- Optional software denoise
- Composition grid
- Crosshair
- Timestamp
- Viewer chrome
- Freeze frame
- Full screen
- Capture and video folder shortcuts
- Interactive About window with Overview, Privacy & Use, Technical and Copyright sections

Image adjustments are software processing only; they do not change the camera hardware's exposure or other physical settings.

## Storage

Default folders:

`Documents\CARE ENT Scope\Captures`

`Documents\CARE ENT Scope\Videos`

## Build on Windows

1. Extract the package.
2. Double-click `build_windows.bat`.
3. The script uses `py -m PyInstaller`, avoiding the PATH problem where `pyinstaller` is installed but its executable directory is not on PATH.
4. The resulting single-file executable is:

`dist\CARE ENT Scope Camera.exe`

Python 3.14 is supported by the current PyInstaller release installed by the user's environment.
