import ctypes
import json
import subprocess
import threading
from ctypes import wintypes

import psutil

from module.base.timer import Timer
from module.logger import logger

PROCESS_IMAGE_NAME = 'MuMuNxDevice.exe'
SW_HIDE = 0

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _get_window_title(hwnd) -> str:
    """Returns the window title, empty if the window has no title."""
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _get_process_image_name(pid) -> str:
    """Returns the full image path of the process, empty if not accessible."""
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        buffer = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ''
    finally:
        kernel32.CloseHandle(handle)


class MuMuWindowHider:
    """Hide the MuMu Player 12 window while the emulator is starting.

    The window shows up 0.6~1.5s after `launch_player`, but `emulator_start_watch()`
    only minimizes it after boot completed, tens of seconds later, covering the WebUI.
    Win32 `ShowWindow(SW_HIDE)` wins the race with window creation. MuMu's own
    `hide_window` is needed once to keep the boss key state in sync, otherwise the
    user has to press Alt+Q twice. That command spawns a console process (~0.45s),
    so it is sent once instead of on every poll. Showing the window is never done
    here, that belongs to the user's boss key.

    Args:
        console_exe (str): Path to MuMuManager.exe
        instance_id (int): MuMu instance index
        instance_name (str): Expected window title
    """

    POLL_INTERVAL = 0.02
    NAME_MATCH_TIMEOUT = 3.0

    def __init__(self, console_exe: str, instance_id: int, instance_name: str):
        self.console_exe = console_exe
        self.instance_id = instance_id
        self.instance_name = instance_name

        # Must be kept on the instance, EnumWindows fails silently if the
        # callback object gets garbage collected.
        self._enum_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(self._enum_windows_callback)

        self._hwnds = set()
        self._baseline_pids = set()
        self._logged_windows = set()
        self._timer = Timer(self.POLL_INTERVAL).start()
        self._stop_event = threading.Event()
        self._thread = None
        self._title_hit = False
        self._official_hide_sent = False

    def start(self) -> None:
        """Start hiding the window. Repeated calls are safe."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._baseline_pids = self._list_baseline_pids()
        logger.info(f'MuMu window hider started, pid baseline: {sorted(self._baseline_pids)}')
        self._stop_event.clear()
        self._timer.reset()
        self._thread = threading.Thread(target=self._run, name='MuMuWindowHider', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop hiding the window.

        Must always be called, even when the start fails. A thread that keeps
        hiding the window would swallow the user's boss key.
        """
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=3)

    def _run(self):
        while not self._stop_event.is_set():
            try:
                # MuMu may show the window again during `starting_vm` /
                # `starting_rom`, press known windows down before scanning.
                for hwnd in list(self._hwnds):
                    user32.ShowWindow(hwnd, SW_HIDE)
                self._scan_windows()
            except Exception as e:
                # Never let this thread die, otherwise the window is sometimes
                # hidden and sometimes not, without any log.
                logger.info(e)
            self._timer.wait()
            self._timer.reset()

    def _scan_windows(self):
        by_pid = not self._title_hit and self._timer.current_time() >= self.NAME_MATCH_TIMEOUT
        for hwnd, title, pid in self._collect_windows():
            if hwnd in self._hwnds or not self._is_target_process(pid, hwnd):
                continue
            if self._match_by_title(title):
                self._title_hit = True
                self._hide(hwnd, title, 'title')
            elif by_pid and pid not in self._baseline_pids:
                self._hide(hwnd, title, 'pid')

    def _collect_windows(self):
        # Returns a list of (hwnd, title, pid) of visible windows.
        self._enum_result = []
        user32.EnumWindows(self._enum_proc, 0)
        return self._enum_result

    def _enum_windows_callback(self, hwnd, lparam):
        try:
            if user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                self._enum_result.append((hwnd, _get_window_title(hwnd).strip(), pid.value))
        except Exception as e:
            logger.info(e)
        return True

    def _match_by_title(self, title) -> bool:
        """Returns True if the window title belongs to the target instance."""
        if not title:
            return False
        if title == self.instance_name:
            return True
        if title.startswith(self.instance_name):
            logger.info(f'Window title "{title}" is not equal to instance name '
                        f'"{self.instance_name}", use prefix match')
            return True
        return False

    def _is_target_process(self, pid, hwnd) -> bool:
        """Returns True if the window belongs to MuMuNxDevice.exe."""
        image = _get_process_image_name(pid)
        if image.lower().endswith(PROCESS_IMAGE_NAME.lower()):
            return True
        if hwnd not in self._logged_windows:
            self._logged_windows.add(hwnd)
            logger.info(f'Skip window hwnd={hwnd}, not {PROCESS_IMAGE_NAME}: {image or "unknown"}')
        return False

    def _hide(self, hwnd, title, method):
        self._hwnds.add(hwnd)
        user32.ShowWindow(hwnd, SW_HIDE)
        logger.info(f'MuMu window hidden: hwnd={hwnd}, title="{title}", match={method}')
        self._send_official_hide()

    def _send_official_hide(self):
        """Send MuMu's own `hide_window` once to keep the boss key state in sync."""
        if self._official_hide_sent:
            return
        self._official_hide_sent = True
        try:
            result = subprocess.run(
                [self.console_exe, 'control', '-v', str(self.instance_id), 'hide_window'],
                capture_output=True, timeout=10
            )
            # Old MuMu versions may miss this subcommand, keep Win32 only.
            data = json.loads(result.stdout.decode('utf-8', errors='ignore') or '{}')
            if data.get('errcode') == 0:
                logger.info(f'MuMuManager hide_window: {data}')
            else:
                logger.warning(f'MuMuManager hide_window failed: {data}')
        except Exception as e:
            logger.warning(f'MuMuManager hide_window failed: {e}')

    @staticmethod
    def _list_baseline_pids():
        # Pids of MuMuNxDevice.exe before the emulator gets launched.
        return {proc.pid for proc in psutil.process_iter(['name'])
                if proc.info['name'] and proc.info['name'].lower() == PROCESS_IMAGE_NAME.lower()}
