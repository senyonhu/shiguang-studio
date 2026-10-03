"""Read the running SDK's own SDL HWND for official child-window embedding.

SDL/Win32 library objects live in this imported module. No native pointer,
window handle or process object is assigned to Ren'Py saved state.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import threading
import time


class SDLVersion(ctypes.Structure):
    _fields_ = [("major", ctypes.c_uint8), ("minor", ctypes.c_uint8), ("patch", ctypes.c_uint8)]


class WindowsInfo(ctypes.Structure):
    _fields_ = [("window", ctypes.c_void_p), ("hdc", ctypes.c_void_p), ("hinstance", ctypes.c_void_p)]


class SysWMUnion(ctypes.Union):
    _fields_ = [("win", WindowsInfo), ("dummy", ctypes.c_uint8 * 64)]


class SysWMInfo(ctypes.Structure):
    _fields_ = [("version", SDLVersion), ("subsystem", ctypes.c_int), ("info", SysWMUnion)]


_api = None


def _windows():
    global _api
    if _api is None:
        if os.name != "nt":
            raise RuntimeError("The embedded HWND probe requires Windows")
        _api = ctypes.WinDLL("user32", use_last_error=True)
        _api.IsWindow.argtypes = [wintypes.HWND]
        _api.IsWindow.restype = wintypes.BOOL
        _api.IsWindowVisible.argtypes = [wintypes.HWND]
        _api.IsWindowVisible.restype = wintypes.BOOL
        _api.IsIconic.argtypes = [wintypes.HWND]
        _api.IsIconic.restype = wintypes.BOOL
        _api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _api.GetWindowThreadProcessId.restype = wintypes.DWORD
        _api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        _api.GetClientRect.restype = wintypes.BOOL
        _api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        _api.GetAncestor.restype = wintypes.HWND
        _api.GetParent.argtypes = [wintypes.HWND]
        _api.GetParent.restype = wintypes.HWND
        _api.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _api.GetClassNameW.restype = ctypes.c_int
        _api.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        _api.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        _api.GetDpiForWindow.argtypes = [wintypes.HWND]
        _api.GetDpiForWindow.restype = wintypes.UINT
        _api.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
        _api.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        _api.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        _api.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        _api.GetForegroundWindow.restype = wintypes.HWND
        _api.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        _api.EnumWindows.restype = wintypes.BOOL
        _api.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
        _api.EnumChildWindows.restype = wintypes.BOOL
    return _api


def _pid(hwnd):
    process_id = wintypes.DWORD()
    _windows().GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return process_id.value


def _rect(hwnd):
    rectangle = wintypes.RECT()
    if not _windows().GetClientRect(hwnd, ctypes.byref(rectangle)):
        raise OSError(ctypes.get_last_error(), "GetClientRect failed for the owned SDK window")
    return [rectangle.left, rectangle.top, rectangle.right, rectangle.bottom]


def read_parent(sdl, window_pointer, renderer=None):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("SDL HWND discovery must run on the SDK main thread")
    if sdl is None or window_pointer is None or not window_pointer.value:
        raise RuntimeError("Ren'Py did not provide its active SDL window")
    if ctypes.sizeof(SysWMInfo) != 72:
        raise RuntimeError("The native window information ABI is not the expected Windows x64 ABI")
    sdl.SDL_GetVersion.argtypes = [ctypes.POINTER(SDLVersion)]
    sdl.SDL_GetVersion.restype = None
    sdl.SDL_GetWindowWMInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(SysWMInfo)]
    sdl.SDL_GetWindowWMInfo.restype = ctypes.c_int
    sdl.SDL_GetWindowID.argtypes = [ctypes.c_void_p]
    sdl.SDL_GetWindowID.restype = ctypes.c_uint32
    sdl.SDL_GetWindowFlags.argtypes = [ctypes.c_void_p]
    sdl.SDL_GetWindowFlags.restype = ctypes.c_uint32
    details = SysWMInfo()
    sdl.SDL_GetVersion(ctypes.byref(details.version))
    if not sdl.SDL_GetWindowWMInfo(window_pointer, ctypes.byref(details)) or details.subsystem != 1:
        raise RuntimeError("SDL could not identify its Windows native window")
    hwnd = int(details.info.win.window or 0)
    api = _windows()
    process_id = os.getpid()
    if not hwnd or not api.IsWindow(hwnd) or _pid(hwnd) != process_id:
        raise RuntimeError("SDL did not return the current SDK process's valid HWND")
    root = int(api.GetAncestor(hwnd, 2) or 0)
    if root != hwnd:
        raise RuntimeError("The SDK main window is not the expected native root")
    awareness = api.GetWindowDpiAwarenessContext(hwnd)
    parent = {"hwnd": hwnd, "process_id": process_id, "root_hwnd": root,
        "sdl_window_id": sdl.SDL_GetWindowID(window_pointer),
        "sdl_version": [details.version.major, details.version.minor, details.version.patch],
        "client_rect": _rect(hwnd), "dpi": api.GetDpiForWindow(hwnd),
        "dpi_awareness_context": int(awareness or 0),
        "dpi_awareness": api.GetAwarenessFromDpiAwarenessContext(awareness),
        "visible": bool(api.IsWindowVisible(hwnd)),
        "sdl_flags": int(sdl.SDL_GetWindowFlags(window_pointer)),
        "minimized": bool(api.IsIconic(hwnd))}
    parent["minimized"] = parent["minimized"] or bool(parent["sdl_flags"] & 0x40)
    if renderer is not None:
        if not all(hasattr(renderer, key) for key in ("physical_box", "physical_size", "virtual_size")):
            raise RuntimeError("The fixed SDK renderer does not expose the required physical viewport")
        parent.update(presentation_from_renderer(parent["client_rect"], renderer.physical_box,
                      renderer.physical_size, renderer.virtual_size, minimized=parent["minimized"]))
    return parent


def presentation_from_renderer(client, physical_box, physical_size, virtual_size, *, minimized=False):
    """Distinguish an owned window's transient absence from invalid geometry.

    No geometry is synthesized while SDL or the renderer has a zero extent.
    Nonzero rectangles still pass the same strict bounds checks as before.
    """
    if len(client) != 4 or len(physical_box) != 4 or len(physical_size) != 2 or len(virtual_size) != 2:
        raise ValueError("Renderer geometry has invalid dimensions")
    values = [*client, *physical_box, *physical_size, *virtual_size]
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
        raise ValueError("Renderer geometry must be finite")
    width, height = client[2] - client[0], client[3] - client[1]
    pw, ph = physical_size
    vx, vy, vw, vh = physical_box
    if (not 0 <= width <= 32768 or not 0 <= height <= 32768 or pw < 0 or ph < 0
            or vx < 0 or vy < 0 or vw < 0 or vh < 0 or virtual_size[0] <= 0 or virtual_size[1] < 10
            or (pw > 0 and vw > 0 and vx + vw > pw + 1)
            or (ph > 0 and vh > 0 and vy + vh > ph + 1)):
        raise ValueError("Renderer viewport is outside the SDL client area")
    if minimized:
        return {"presentable": False, "presentation_reason": "minimized"}
    if width == 0 or height == 0:
        return {"presentable": False, "presentation_reason": "zero_client"}
    if pw == 0 or ph == 0 or vw == 0 or vh == 0:
        return {"presentable": False, "presentation_reason": "renderer_resize_pending"}
    return {"presentable": True, "presentation_reason": "ready",
            "geometry": geometry_from_renderer(client, physical_box, physical_size, virtual_size)}


def geometry_from_renderer(client, physical_box, physical_size, virtual_size):
    """Map the renderer's real letterboxed canvas into matching HWND units."""
    values = [*client, *physical_box, *physical_size, *virtual_size]
    if len(client) != 4 or len(physical_box) != 4 or len(physical_size) != 2 or len(virtual_size) != 2:
        raise ValueError("Renderer geometry has invalid dimensions")
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
        raise ValueError("Renderer geometry must be finite")
    width, height = client[2] - client[0], client[3] - client[1]
    pw, ph = physical_size
    vx, vy, vw, vh = physical_box
    if not (0 < width <= 32768 and 0 < height <= 32768 and pw > 0 and ph > 0
            and vx >= 0 and vy >= 0 and vw > 0 and vh > 0 and vx + vw <= pw + 1 and vy + vh <= ph + 1
            and virtual_size[0] > 0 and virtual_size[1] >= 10):
        raise ValueError("Renderer viewport is outside the SDL client area")
    sx, sy = width / pw, height / ph
    left, top = math.ceil(vx * sx), math.ceil(vy * sy)
    right, bottom = min(int(width), math.floor((vx + vw) * sx)), min(int(height), math.floor((vy + vh) * sy))
    footer_virtual = int(virtual_size[1] * 0.1)
    boundary = math.floor((vy + vh * (1 - footer_virtual / virtual_size[1])) * sy)
    if right <= left or boundary <= top or bottom <= boundary:
        raise ValueError("Renderer viewport cannot fit the HTML layer and story toolbar")
    return {"parent_client": [int(width), int(height)], "viewport": [left, top, right - left, boundary - top],
            "footer": [left, boundary, right - left, bottom - boundary]}


def restore_parent_focus(parent):
    """Focus only this game's existing SDL root, without stealing foreground."""
    api = _windows()
    hwnd = parent["hwnd"]
    if not api.IsWindow(hwnd) or _pid(hwnd) != os.getpid():
        return False
    foreground = api.GetForegroundWindow()
    if not foreground or int(api.GetAncestor(foreground, 2) or 0) != hwnd:
        return False
    api.SetFocus.argtypes = [wintypes.HWND]
    api.SetFocus.restype = wintypes.HWND
    api.GetFocus.restype = wintypes.HWND
    api.SetFocus(hwnd)
    return int(api.GetFocus() or 0) == hwnd
