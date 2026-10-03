"""Geometry and owned-window boundaries; these are not physical UI evidence."""
import ctypes
import os
from types import SimpleNamespace

import pytest

from renpy_bridge import minigame_windows as windows


@pytest.mark.parametrize('client,box,size,minimized,reason', [
    ([0, 0, 0, 0], (0, 0, 1280, 720), (1280, 720), False, 'zero_client'),
    ([0, 0, 0, 720], (0, 0, 1280, 720), (1280, 720), False, 'zero_client'),
    ([0, 0, 1280, 0], (0, 0, 1280, 720), (1280, 720), False, 'zero_client'),
    ([0, 0, 1280, 720], (0, 0, 1280, 720), (1280, 720), True, 'minimized'),
    ([0, 0, 1280, 720], (0, 0, 0, 0), (0, 0), False, 'renderer_resize_pending'),
    ([0, 0, 1280, 720], (0, 0, 0, 0), (1280, 720), False, 'renderer_resize_pending'),
])
def test_unpresentable_owned_window_has_no_invented_geometry(client, box, size, minimized, reason):
    result = windows.presentation_from_renderer(client, box, size, (1920, 1080), minimized=minimized)
    assert result == {'presentable': False, 'presentation_reason': reason}


@pytest.mark.parametrize('client,box,size', [
    ([0, 0, -1, 0], (0, 0, 1280, 720), (1280, 720)),
    ([0, 0, 1280, 720], (-1, 0, 1280, 720), (1280, 720)),
    ([0, 0, 1280, 720], (0, 0, 1282, 720), (1280, 720)),
    ([0, 0, 1280, 720], (0, 0, 1280, 722), (1280, 720)),
    ([0, 0, 0, 0], (0, 0, 1282, 720), (1280, 720)),
    ([0, 0, 1280, 720], (0, 0, -1, 720), (1280, 720)),
    ([0, 0, 1280, 720], (0, 0, 1280, 720), (-1, 720)),
    ([0, 0, 1280, 720], (0, float('nan'), 1280, 720), (1280, 720)),
])
def test_minimized_flag_cannot_hide_invalid_nonzero_or_nonfinite_geometry(client, box, size):
    with pytest.raises(ValueError):
        windows.presentation_from_renderer(client, box, size, (1920, 1080), minimized=True)


def test_valid_geometry_remains_the_actual_native_renderer_mapping():
    result = windows.presentation_from_renderer([0, 0, 1024, 900], (0, 162, 1024, 576), (1024, 900), (1920, 1080))
    assert result['presentable'] is True
    assert result['geometry'] == {'parent_client': [1024, 900], 'viewport': [0, 162, 1024, 518], 'footer': [0, 680, 1024, 58]}


def fake_platform(monkeypatch, *, iconic=False, flags=0, valid=True, pid=None, root=42):
    def client(hwnd, rect):
        rect._obj.left = rect._obj.top = rect._obj.right = rect._obj.bottom = 0
        return 1
    def process(hwnd, pointer):
        pointer._obj.value = os.getpid() if pid is None else pid
        return 1
    api = SimpleNamespace(IsWindow=lambda hwnd: valid, GetClientRect=client, GetWindowThreadProcessId=process,
        GetAncestor=lambda hwnd, kind: root, IsWindowVisible=lambda hwnd: True, IsIconic=lambda hwnd: iconic,
        GetDpiForWindow=lambda hwnd: 96, GetWindowDpiAwarenessContext=lambda hwnd: 1,
        GetAwarenessFromDpiAwarenessContext=lambda value: 1)
    def version(pointer):
        pointer._obj.major = 2
    def info(pointer, details):
        details._obj.subsystem = 1
        details._obj.info.win.window = 42
        return 1
    def window_id(pointer):
        return 1
    def window_flags(pointer):
        return flags
    sdl = SimpleNamespace(SDL_GetVersion=version, SDL_GetWindowWMInfo=info,
                          SDL_GetWindowID=window_id, SDL_GetWindowFlags=window_flags)
    monkeypatch.setattr(windows, '_windows', lambda: api)
    return sdl


@pytest.mark.parametrize('iconic,flags', [(True, 0), (False, 0x40), (False, 0)])
def test_native_zero_client_observation_preserves_actual_identity(monkeypatch, iconic, flags):
    sdl = fake_platform(monkeypatch, iconic=iconic, flags=flags)
    renderer = SimpleNamespace(physical_box=(0, 0, 1280, 720), physical_size=(1280, 720), virtual_size=(1920, 1080))
    parent = windows.read_parent(sdl, ctypes.c_void_p(1), renderer)
    assert parent['hwnd'] == parent['root_hwnd'] == 42 and parent['process_id'] == os.getpid()
    assert parent['client_rect'] == [0, 0, 0, 0] and 'geometry' not in parent
    assert parent['presentation_reason'] == ('minimized' if iconic or flags & 0x40 else 'zero_client')


@pytest.mark.parametrize('kwargs', [{'valid': False}, {'pid': 999999}, {'root': 55}])
def test_zero_client_does_not_excuse_lost_foreign_or_nonroot_window(monkeypatch, kwargs):
    sdl = fake_platform(monkeypatch, **kwargs)
    renderer = SimpleNamespace(physical_box=(0, 0, 1280, 720), physical_size=(1280, 720), virtual_size=(1920, 1080))
    with pytest.raises(RuntimeError):
        windows.read_parent(sdl, ctypes.c_void_p(1), renderer)
