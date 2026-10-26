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
SKIP_LOG_LIMIT = 20

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

    Windows are matched by pid, not by title. The title is an alias the user sets
    in the multi-instance manager (e.g. "一"), which differs from the config folder
    name (`MuMuPlayer-12.0-0`) and can be renamed at any time, so title matching
    misses every renamed instance.

    A pid becomes a target when it is not in the pid snapshot taken before launch,
    or when one of its windows carries the alias reported by `MuMuManager info`.
    All visible top-level windows of a target pid are hidden, as one instance owns
    several of them.

    Win32 `ShowWindow(SW_HIDE)` wins the race with window creation. MuMu's own
    `hide_window` is sent once by `finish()` after boot completed, so the boss key
    state is aligned at the moment the hider stops. It is never sent while polling,
    as each call spawns a console process (~0.45s).

    Showing the window is never done here, that belongs to the user's boss key.

    Args:
        console_exe (str): Path to MuMuManager.exe
        instance_id (int): MuMu instance index
        instance_name (str): Instance folder name, used as an extra alias candidate
    """

    POLL_INTERVAL = 0.02

    def __init__(self, console_exe: str, instance_id: int, instance_name: str):
        self.console_exe = console_exe
        self.instance_id = instance_id
        self.instance_name = instance_name

        # Must be kept on the instance, EnumWindows fails silently if the
        # callback object gets garbage collected.
        self._enum_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(self._enum_windows_callback)

        self._hwnds = set()
        self._target_pids = set()
        self._alias_names = set()
        self._baseline_pids = set()
        self._logged_pids = set()
        self._skip_logged = 0
        self._skip_suppressed = 0
        self._timer = Timer(self.POLL_INTERVAL).start()
        self._stop_event = threading.Event()
        self._thread = None
        self._official_hide_sent = False

    def start(self) -> None:
        """Start hiding the window. Repeated calls are safe."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._baseline_pids = self._list_baseline_pids()
        self._alias_names = self._read_alias_names()
        logger.info(f'MuMu window hider started, pid baseline: {sorted(self._baseline_pids)}, '
                    f'alias: {sorted(self._alias_names)}')
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
        if self._skip_suppressed:
            logger.info(f'MuMu window hider: {self._skip_suppressed} skip messages suppressed')

    def finish(self) -> None:
        """Hide the instance for good after boot completed.

        Minimizing (`SW_MINIMIZE`) leaves an icon in the taskbar, only `SW_HIDE`
        gives a window without taskbar presence, and the boss key is the only way
        to bring it back.
        """
        self._hide_target_windows()
        self._send_official_hide()
        for hwnd in list(self._hwnds):
            title = _get_window_title(hwnd)
            if user32.IsWindowVisible(hwnd):
                logger.warning(f'MuMu window still visible: hwnd={hwnd}, title="{title}"')
            else:
                logger.info(f'MuMu window hidden after start: hwnd={hwnd}, title="{title}"')

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
        for hwnd, title, pid in self._collect_windows():
            if hwnd in self._hwnds:
                continue
            if pid in self._target_pids:
                self._hide(hwnd, title, 'pid')
                continue
            if not self._is_target_process(pid, title):
                continue
            if pid not in self._baseline_pids:
                self._target_pids.add(pid)
                self._hide(hwnd, title, 'pid')
            elif title in self._alias_names:
                self._target_pids.add(pid)
                self._hide(hwnd, title, 'alias')

    def _hide_target_windows(self):
        for hwnd, title, pid in self._collect_windows():
            if pid in self._target_pids:
                self._hwnds.add(hwnd)
                user32.ShowWindow(hwnd, SW_HIDE)

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

    def _is_target_process(self, pid, title) -> bool:
        """Returns True if the window belongs to MuMuNxDevice.exe."""
        image = _get_process_image_name(pid)
        if image.lower().endswith(PROCESS_IMAGE_NAME.lower()):
            return True
        self._log_skip(pid, title, image)
        return False

    def _log_skip(self, pid, title, image):
        """Log skipped windows of the MuMu family only, other apps change every poll."""
        if 'mumu' not in image.lower():
            return
        if pid in self._logged_pids:
            return
        self._logged_pids.add(pid)
        if self._skip_logged < SKIP_LOG_LIMIT:
            self._skip_logged += 1
            logger.info(f'Skip window pid={pid}, title="{title}", not {PROCESS_IMAGE_NAME}: {image}')
        else:
            self._skip_suppressed += 1

    def _hide(self, hwnd, title, method):
        self._hwnds.add(hwnd)
        user32.ShowWindow(hwnd, SW_HIDE)
        logger.info(f'MuMu window hidden: hwnd={hwnd}, title="{title}", match={method}')

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

    def _read_alias_names(self):
        """Read the instance alias from `MuMuManager info`, empty on failure."""
        names = set()
        if self.instance_name:
            names.add(self.instance_name)
        try:
            result = subprocess.run(
                [self.console_exe, 'info', '-v', str(self.instance_id)],
                capture_output=True, timeout=10
            )
            stdout = result.stdout.decode('utf-8', errors='ignore')
            for line in stdout.splitlines():
                key, sep, value = line.partition('=')
                if sep and key.strip().strip('"') in ('name', '"name"'):
                    names.add(value.strip().strip('",'))
            # Newer versions print a single JSON object instead of `key = value`
            if stdout.strip().startswith('{'):
                data = json.loads(stdout)
                if data.get('name'):
                    names.add(str(data['name']))
        except Exception as e:
            logger.info(f'MuMuManager info failed, fallback to instance name: {e}')
        return names

    @staticmethod
    def _list_baseline_pids():
        # Pids of MuMuNxDevice.exe before the emulator gets launched.
        return {proc.pid for proc in psutil.process_iter(['name'])
                if proc.info['name'] and proc.info['name'].lower() == PROCESS_IMAGE_NAME.lower()}
