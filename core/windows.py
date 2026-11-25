"""Windows input and foreground-only capture, in physical screen pixels."""
import ctypes
from ctypes import wintypes as W
import threading
import time
from PIL import ImageGrab

user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.GetForegroundWindow.restype = W.HWND
user32.WindowFromPoint.argtypes = [W.POINT]
user32.WindowFromPoint.restype = W.HWND
user32.GetAncestor.argtypes = [W.HWND, W.UINT]
user32.GetAncestor.restype = W.HWND
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.RegisterHotKey.argtypes = [W.HWND, ctypes.c_int, W.UINT, W.UINT]
user32.UnregisterHotKey.argtypes = [W.HWND, ctypes.c_int]


class MonitorInfo(ctypes.Structure):
    _fields_ = [('size', W.DWORD), ('monitor', W.RECT), ('work', W.RECT), ('flags', W.DWORD)]


def centered_bounds(work, width, height):
    left, top, right, bottom = work
    width, height = min(width, right - left), min(height, bottom - top)
    return left + (right - left - width) // 2, top + (bottom - top - height) // 2, width, height


def center_window(hwnd, client_size, design_size):
    """Place the outer window in the pointer monitor's physical work area."""
    cursor = W.POINT()
    user32.MonitorFromPoint.argtypes = [W.POINT, W.DWORD]
    user32.MonitorFromPoint.restype = W.HANDLE
    user32.GetMonitorInfoW.argtypes = [W.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    user32.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, W.UINT]
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    outer = W.RECT()
    if (not user32.GetCursorPos(ctypes.byref(cursor))
            or not user32.GetMonitorInfoW(user32.MonitorFromPoint(cursor, 2), ctypes.byref(info))
            or not user32.GetWindowRect(hwnd, ctypes.byref(outer))):
        return None
    # Establish the destination DPI before measuring the frame for final placement.
    user32.SetWindowPos(hwnd, None, info.work.left, info.work.top, 0, 0, 0x0015)
    client = W.RECT()
    user32.GetClientRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    user32.GetClientRect(hwnd, ctypes.byref(client))
    frame_width = outer.right - outer.left - client.right
    frame_height = outer.bottom - outer.top - client.bottom
    get_dpi = getattr(user32, 'GetDpiForWindow', None)
    if get_dpi:
        get_dpi.argtypes = [W.HWND]
    dpi = get_dpi(hwnd) if get_dpi else 0
    scale = dpi / 96 if dpi else client_size[0] / design_size[0]
    work = info.work
    placement = centered_bounds((work.left, work.top, work.right, work.bottom),
                                round(design_size[0] * scale) + frame_width,
                                round(design_size[1] * scale) + frame_height)
    if user32.SetWindowPos(hwnd, None, *placement, 0x0014):
        return int((placement[2] - frame_width) / scale), int((placement[3] - frame_height) / scale)
    return None

class StopHotkey:
    """Register F8 on the listener thread with Windows key-repeat suppression."""
    def __init__(self):
        self.registered = bool(user32.RegisterHotKey(None, 0xB17, 0x4000, 0x77))
        self.was_down = False
        if not self.registered:
            self.was_down = bool(user32.GetAsyncKeyState(0x77) & 0x8000)

    def pressed(self):
        if not self.registered:
            state = user32.GetAsyncKeyState(0x77)
            down = bool(state & 0x8000)
            pressed = not self.was_down and bool(state & 0x8001)
            self.was_down = down
            return pressed
        message = W.MSG()
        while user32.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
            if message.wParam == 0xB17:
                return True
        return False

    def close(self):
        if self.registered:
            user32.UnregisterHotKey(None, 0xB17)

def dpi_aware():
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()

def keep_awake(enabled):
    """Only while the worker is running; restore normal power policy afterwards."""
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000003 if enabled else 0x80000000)

def single_instance():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
    kernel.CreateMutexW.restype = W.HANDLE
    handle = kernel.CreateMutexW(None, False, 'Local\\ObeliskCompanion')
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle.argtypes = [W.HANDLE]
        kernel.CloseHandle(handle)
        return None
    return handle  # Kept alive by the entry point until process exit.

class MouseInput(ctypes.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD),
                ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', ctypes.c_size_t)]
class KeyboardInput(ctypes.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', ctypes.c_size_t)]
class InputUnion(ctypes.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyboardInput)]
class Input(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', W.DWORD), ('u', InputUnion)]

class Desktop:
    def __init__(self):
        self.hwnd = None
        self.held = False
        self.lock = threading.RLock()
        self.rect = None
        self.abort = threading.Event()

    def find(self):
        matches = []
        callback_type = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        @callback_type
        def callback(hwnd, _):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            if user32.IsWindowVisible(hwnd) and buf.value.startswith('Idle Obelisk Miner'):
                matches.append((hwnd, buf.value))
            return True
        user32.EnumWindows(callback, 0)
        if len(matches) != 1:
            raise RuntimeError(f'Expected one game window; found {len(matches)}.')
        self.hwnd, title = matches[0]
        return title

    def focus(self):
        if not self.hwnd:
            self.find()
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)
        return bool(user32.SetForegroundWindow(self.hwnd))

    def foreground(self):
        return self.hwnd is not None and user32.GetForegroundWindow() == self.hwnd

    def bounds(self):
        rect = W.RECT()
        if not user32.GetClientRect(self.hwnd, ctypes.byref(rect)):
            raise RuntimeError('The game window is closed')
        point = W.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(point))
        return point.x, point.y, point.x + rect.right, point.y + rect.bottom

    def capture(self):
        if not self.foreground() or user32.IsIconic(self.hwnd):
            raise RuntimeError('The game lost focus. Mouse released; restart when ready.')
        bounds = self.bounds()
        if self.held and bounds != self.rect:
            raise RuntimeError('The game window moved while the mouse was held.')
        self.rect = bounds
        return ImageGrab.grab(bbox=bounds, all_screens=True)

    def _event(self, flags):
        event = Input(type=0, u=InputUnion(mi=MouseInput(0, 0, 0, flags, 0, 0)))
        if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            raise RuntimeError('Windows rejected mouse input')

    def point(self, position, viewport):
        if not self.foreground() or self.bounds() != self.rect:
            raise RuntimeError('The window changed after the last screen reading')
        l, t, r, b = viewport
        x = self.rect[0] + l + round(position[0] * (r-l)/1200)
        y = self.rect[1] + t + round(position[1] * (b-t)/800)
        target = user32.WindowFromPoint(W.POINT(x, y))
        if user32.GetAncestor(target, 2) != self.hwnd:
            raise RuntimeError('Another window covers the click target')
        return x, y

    def hold(self, position, viewport):
        with self.lock:
            if self.abort.is_set():
                return
            if self.held:
                return
            x, y = self.point(position, viewport)
            user32.SetCursorPos(x, y)
            self._event(0x0002)
            self.held = True

    def release(self):
        with self.lock:
            if self.held:
                self._event(0x0004)
                self.held = False

    def click(self, position, viewport):
        self.release()
        self.hold(position, viewport)
        try:
            time.sleep(.07)
        finally:
            self.release()

    def click_current(self):
        with self.lock:
            if self.abort.is_set() or not self.foreground() or self.bounds() != self.rect:
                raise RuntimeError('The game window changed or the session was stopped.')
            self._event(0x0002)
            self.held = True
        try:
            time.sleep(.07)
        finally:
            self.release()

    @staticmethod
    def stop_pressed():
        # Include a press since the last poll, including very short key taps.
        return bool(user32.GetAsyncKeyState(0x77) & 0x8001)  # F8


def wait_for_game_focus(desktop, stopped, timeout=2):
    deadline = time.monotonic() + timeout
    while not stopped():
        if desktop.foreground():
            return True
        if time.monotonic() >= deadline:
            raise RuntimeError('Windows did not bring the selected game window to the foreground.')
        time.sleep(.05)
    return False


