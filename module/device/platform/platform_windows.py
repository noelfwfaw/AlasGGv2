import ctypes
import json
import os
import re
import subprocess

import psutil

from deploy.Windows.utils import DataProcessInfo
from module.base.decorator import del_cached_property, run_once
from module.base.timer import Timer
from module.device.connection import AdbDeviceWithStatus
from module.device.platform.platform_base import PlatformBase
from module.device.platform.emulator_windows import Emulator, EmulatorInstance, EmulatorManager
from module.device.platform.mumu_window_hider import MuMuWindowHider, set_foreground_window
from module.logger import logger


class EmulatorUnknown(Exception):
    pass


def get_focused_window():
    return ctypes.windll.user32.GetForegroundWindow()


def set_focus_window(hwnd):
    # A bare SetForegroundWindow from a background process is blocked by the
    # Windows foreground lock, use the AttachThreadInput variant instead.
    set_foreground_window(hwnd)


def minimize_window(hwnd):
    ctypes.windll.user32.ShowWindow(hwnd, 6)


def get_window_title(hwnd):
    """Returns the window title as a string."""
    text_len_in_characters = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    string_buffer = ctypes.create_unicode_buffer(
        text_len_in_characters + 1)  # +1 for the \0 at the end of the null-terminated string.
    ctypes.windll.user32.GetWindowTextW(hwnd, string_buffer, text_len_in_characters + 1)
    return string_buffer.value


def flash_window(hwnd, flash=True):
    ctypes.windll.user32.FlashWindow(hwnd, flash)


class PlatformWindows(PlatformBase, EmulatorManager):
    @classmethod
    def execute(cls, command):
        """
        Args:
            command (str):

        Returns:
            subprocess.Popen:
        """
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        logger.info(f'Execute: {command}')
        # `close_fds` only work on Windows
        # `start_new_session` to avoid emulator getting tree-killed when Alas gets killed
        return subprocess.Popen(command, close_fds=True, start_new_session=True)

    @classmethod
    def kill_process_by_regex(cls, regex: str) -> int:
        """
        Kill processes with cmdline match the given regex.

        Args:
            regex:

        Returns:
            int: Number of processes killed
        """
        count = 0

        for proc in psutil.process_iter():
            cmdline = DataProcessInfo(proc=proc, pid=proc.pid).cmdline
            if re.search(regex, cmdline):
                logger.info(f'Kill emulator: {cmdline}')
                proc.kill()
                count += 1

        return count

    @classmethod
    def _parse_mumu_api_output(cls, stdout: bytes):
        """
        Extract the JSON envelope printed by MuMuManager.

        MuMuManager prints one JSON object per call, such as
        `{"errcode":0,"errmsg":""}`. Older versions may print extra lines
        before it, so scan from the bottom and keep the first parsable object
        that carries `errcode`.

        Args:
            stdout (bytes): Raw standard output of MuMuManager

        Returns:
            dict | None: Parsed envelope, None if no envelope was found
        """
        text = stdout.decode('utf-8', errors='ignore')
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith('{') or not line.endswith('}'):
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and 'errcode' in data:
                return data
        return None

    @classmethod
    def _mumu_manager_api(cls, console: str, instance_id: int, action: str, timeout: int = 20) -> bool:
        """
        Run a MuMuManager api command and check whether MuMu accepted it.

        `execute()` only spawns a process and never looks at its exit code, so a
        dropped `launch_player` was logged as if it succeeded and ALAS waited
        180s for nothing. Run the command in the foreground, read its JSON
        envelope and report failure upward.

        Keeping the command synchronous also serializes it with the previous
        `shutdown_player`: MuMu starts a temporary API host per call, two hosts
        launched within a few milliseconds (cold backend) race and the later one
        can be torn down before it dispatches its action.

        Args:
            console (str): Path to MuMuManager.exe
            instance_id (int): MuMu instance index
            action (str): MuMu api action, such as launch_player
            timeout (int): Seconds to wait for the command to exit

        Returns:
            bool: True if MuMu accepted the command
        """
        command = [console, 'api', '-v', str(instance_id), action]
        logger.info(f'Execute: {" ".join(command)}')
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning(f'MuMuManager {action} timeout after {timeout}s')
            return False

        stdout = result.stdout or b''
        output = stdout.decode('utf-8', errors='ignore').strip()
        if output:
            logger.info(f'MuMuManager {action}: {output}')

        data = cls._parse_mumu_api_output(stdout)
        if data is not None:
            errcode = data.get('errcode')
            if errcode == 0:
                return True
            errmsg = str(data.get('errmsg', data.get('message', '')))
            # Stopping an instance that is already stopped is the desired state,
            # `emulator_start()` always issues a stop before the launch. Match
            # the message instead of the numeric code, MuMu versions report
            # different codes for the same condition.
            if action == 'shutdown_player' and 'player not running' in errmsg:
                logger.info(f'MuMuPlayer is already stopped: {data}')
                return True
            logger.warning(f'MuMuManager {action} failed: {data}')
            return False

        # Older MuMu versions print nothing or plain key=value lines, fall back
        # to the exit code instead of treating a missing envelope as failure.
        if result.returncode != 0:
            logger.warning(f'MuMuManager {action} failed, returncode {result.returncode}')
            return False
        return True

    def _emulator_start(self, instance: EmulatorInstance):
        """
        Start a emulator without error handling
        """
        exe: str = instance.emulator.path
        if instance == Emulator.MuMuPlayer:
            # NemuPlayer.exe
            self.execute(exe)
        elif instance == Emulator.MuMuPlayerX:
            # NemuPlayer.exe -m nemu-12.0-x64-default
            self.execute(f'"{exe}" -m {instance.name}')
        elif instance == Emulator.MuMuPlayer12:
            # MuMuManager.exe api -v 0 launch_player
            # Launch via MuMuManager instead of MuMuPlayer.exe/MuMuNxMain.exe.
            # MuMuNxMain.exe is a GUI singleton, if two instances get launched at the same time,
            # the second launch request is handed over to a MuMuNxMain.exe that is still initializing
            # and gets silently dropped, while MuMuManager queues requests in backend service.
            if instance.MuMuPlayer12_id is None:
                logger.warning(f'Cannot get MuMu instance index from name {instance.name}')
                return False
            if not self._mumu_manager_api(
                Emulator.single_to_console(exe), instance.MuMuPlayer12_id, 'launch_player'
            ):
                return False
        elif instance == Emulator.LDPlayer14 or instance == Emulator.LDPlayer9:
            # ldconsole.exe launch --index 0 --mini
            # LDPlayer above 9 has `--mini` to start as minimized window, `--hide` to start with no frontend window
            self.execute(f'"{Emulator.single_to_console(exe)}" launch --index {instance.LDPlayer_id} --mini')
        elif instance == Emulator.LDPlayerFamily:
            # ldconsole.exe launch --index 0
            self.execute(f'"{Emulator.single_to_console(exe)}" launch --index {instance.LDPlayer_id}')
        elif instance == Emulator.NoxPlayerFamily:
            # Nox.exe -clone:Nox_1
            self.execute(f'"{exe}" -clone:{instance.name}')
        elif instance == Emulator.BlueStacks5:
            # HD-Player.exe --instance Pie64
            self.execute(f'"{exe}" --instance {instance.name}')
        elif instance == Emulator.BlueStacks4:
            # Bluestacks.exe -vmname Android_1
            self.execute(f'"{exe}" -vmname {instance.name}')
        elif instance == Emulator.MEmuPlayer:
            # MEmu.exe MEmu_0
            self.execute(f'"{exe}" {instance.name}')
        else:
            raise EmulatorUnknown(f'Cannot start an unknown emulator instance: {instance}')

    def _emulator_stop(self, instance: EmulatorInstance):
        """
        Stop a emulator without error handling
        """
        exe: str = instance.emulator.path
        if instance == Emulator.MuMuPlayer:
            # MuMu6 does not have multi instance, kill one means kill all
            # Has 4 processes
            # "C:\Program Files\NemuVbox\Hypervisor\NemuHeadless.exe" --comment nemu-6.0-x64-default --startvm
            # "E:\ProgramFiles\MuMu\emulator\nemu\EmulatorShell\NemuPlayer.exe"
            # E:\ProgramFiles\MuMu\emulator\nemu\EmulatorShell\NemuService.exe
            # "C:\Program Files\NemuVbox\Hypervisor\NemuSVC.exe" -Embedding
            self.kill_process_by_regex(
                rf'('
                rf'NemuHeadless.exe'
                rf'|NemuPlayer.exe\"'
                rf'|NemuPlayer.exe$'
                rf'|NemuService.exe'
                rf'|NemuSVC.exe'
                rf')'
            )
        elif instance == Emulator.MuMuPlayerX:
            # MuMu X has 3 processes
            # "E:\ProgramFiles\MuMu9\emulator\nemu9\EmulatorShell\NemuPlayer.exe" -m nemu-12.0-x64-default -s 0 -l
            # "C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6Headless.exe" --comment nemu-12.0-x64-default --startvm xxx
            # "C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6SVC.exe" --Embedding
            self.kill_process_by_regex(
                rf'('
                rf'NemuPlayer.exe.*-m {instance.name}'
                rf'|Muvm6Headless.exe'
                rf'|Muvm6SVC.exe'
                rf')'
            )
        elif instance == Emulator.MuMuPlayer12:
            # MuMuManager.exe api -v 1 shutdown_player
            if instance.MuMuPlayer12_id is None:
                logger.warning(f'Cannot get MuMu instance index from name {instance.name}')
                return False
            # Shutdown stays best-effort: `emulator_stop()` retries by starting
            # the emulator when a stop reports failure, so a rejected stop must
            # not abort the close path. Calling the helper synchronously still
            # serializes the command with the launch that follows, and MuMu's
            # envelope is logged either way.
            self._mumu_manager_api(
                Emulator.single_to_console(exe), instance.MuMuPlayer12_id, 'shutdown_player'
            )
        elif instance == Emulator.LDPlayerFamily:
            # ldconsole.exe quit --index 0
            self.execute(f'"{Emulator.single_to_console(exe)}" quit --index {instance.LDPlayer_id}')
        elif instance == Emulator.NoxPlayerFamily:
            # Nox.exe -clone:Nox_1 -quit
            self.execute(f'"{exe}" -clone:{instance.name} -quit')
        elif instance == Emulator.BlueStacks5:
            # BlueStack has 2 processes
            # C:\Program Files\BlueStacks_nxt_cn\HD-Player.exe --instance Pie64
            # C:\Program Files\BlueStacks_nxt_cn\BstkSVC.exe -Embedding
            self.kill_process_by_regex(
                rf'('
                rf'HD-Player.exe.*"--instance" "{instance.name}"'
                rf')'
            )
        elif instance == Emulator.BlueStacks4:
            # E:\Program Files (x86)\BluestacksCN\bsconsole.exe quit --name Android
            self.execute(f'"{Emulator.single_to_console(exe)}" quit --name {instance.name}')
        elif instance == Emulator.MEmuPlayer:
            # F:\Program Files\Microvirt\MEmu\memuc.exe stop -n MEmu_0
            self.execute(f'"{Emulator.single_to_console(exe)}" stop -n {instance.name}')
        else:
            raise EmulatorUnknown(f'Cannot stop an unknown emulator instance: {instance}')

    def _emulator_function_wrapper(self, func: callable):
        """
        Args:
            func (callable): _emulator_start or _emulator_stop

        Returns:
            bool: If success
        """
        try:
            result = func(self.emulator_instance)
        except OSError as e:
            msg = str(e)
            # OSError: [WinError 740] 请求的操作需要提升。
            if 'WinError 740' in msg:
                logger.error('To start/stop MumuAppPlayer, ALAS needs to be run as administrator')
        except EmulatorUnknown as e:
            logger.error(e)
        except Exception as e:
            logger.exception(e)
        else:
            # `_emulator_start()` / `_emulator_stop()` return False when the
            # command was delivered but rejected (e.g. MuMuManager errcode != 0).
            # Other branches return None, which still means success.
            if result is not False:
                return True

        logger.error(f'Emulator function {func.__name__}() failed')
        return False

    def refresh_serial_by_name(self):
        """
        按实例名重新枚举，返回模拟器当前真实的 adb 端口。

        MuMu 每次启动都会重写 .nemu 里的 ADB_PORT（实测同一天内端口会在两个值之间来回换），
        此时按 serial 已经找不到实例，只能按 name 匹配。

        Returns:
            str | None: 当前 serial，找不到实例时返回 None
        """
        name = self.emulator_info.name
        if not name:
            return None
        # all_emulator_instances / all_emulator_serials 都是 cached_property，
        # 不清缓存就永远读到启动前枚举出来的旧端口
        del_cached_property(self, 'all_emulator_instances')
        del_cached_property(self, 'all_emulator_serials')
        try:
            instances = self.all_emulator_instances
        except Exception as e:
            logger.info(e)
            return None
        for instance in instances:
            if instance.name == name:
                return instance.serial
        return None

    def emulator_start_watch(self, window_hider=None, current_window=None):
        """
        Args:
            window_hider (MuMuWindowHider): Keep the window hidden when set,
                minimize it by default.
            current_window (int): Foreground window captured before the launch
                command. Passed in because the emulator may already own the
                foreground by the time this loop starts.

        Returns:
            bool: True if startup completed
                False if timeout
        """
        logger.hr('Emulator start', level=2)
        if current_window is None:
            current_window = get_focused_window()
        serial = self.emulator_instance.serial
        logger.info(f'Current window: {current_window}')

        def adb_connect():
            m = self.adb_client.connect(self.serial)
            if 'connected' in m:
                # Connected to 127.0.0.1:59865
                # Already connected to 127.0.0.1:59865
                return False
            elif '(10061)' in m:
                # cannot connect to 127.0.0.1:55555:
                # No connection could be made because the target machine actively refused it. (10061)
                return False
            else:
                return True

        @run_once
        def show_online(m):
            logger.info(f'Emulator online: {m}')

        @run_once
        def show_ping(m):
            logger.info(f'Command ping: {m}')

        @run_once
        def show_package(m):
            logger.info(f'Found azurlane packages: {m}')

        @run_once
        def show_boot_wait(m):
            logger.info(f'Waiting system boot completed: {m}')

        interval = Timer(0.5).start()
        timeout = Timer(180).start()
        progress_timer = Timer(10).start()
        serial_timer = Timer(10).start()
        new_window = 0
        while 1:
            interval.wait()
            interval.reset()
            if timeout.reached():
                logger.warning(f'Emulator start timeout')
                return False

            # Check emulator window showing up
            # logger.info([get_focused_window(), get_window_title(get_focused_window())])
            if current_window != 0 and new_window == 0:
                new_window = get_focused_window()
                if current_window != new_window:
                    logger.info(f'New window showing up: {new_window}, focus back')
                    set_focus_window(current_window)
                else:
                    new_window = 0

            # Check device connection
            devices = self.list_device().select(serial=serial)
            if progress_timer.reached():
                progress_timer.reset()
                logger.info(
                    f'Waiting emulator online {int(timeout.current_time())}s/180s: {serial} '
                    f'(devices: {[d.serial for d in devices] if devices else "none"})'
                )
            # logger.info(devices)
            if devices:
                device: AdbDeviceWithStatus = devices.first_or_none()
                if device.status == 'device':
                    # Emulator online
                    pass
                if device.status == 'offline':
                    self.adb_client.disconnect(serial)
                    adb_connect()
                    continue
            else:
                # 启动期间 MuMu 会重写 .nemu 里的 ADB_PORT，启动前的端口可能已经变了，
                # 只认启动前快照的 serial 会一直连到 180s 超时。按实例名重发现，节流 10 秒。
                if serial_timer.reached():
                    serial_timer.reset()
                    new_serial = self.refresh_serial_by_name()
                    if new_serial and new_serial != serial:
                        logger.warning(f'Emulator serial changed: {serial} -> {new_serial}')
                        serial = new_serial
                        # adb_connect() 用的是 self.serial，必须同步改掉
                        self.config.Emulator_Serial = self.serial = new_serial
                        del_cached_property(self, 'emulator_instance')
                        del_cached_property(self, 'adb')
                # Try to connect
                adb_connect()
                continue
            show_online(devices.first_or_none())

            # Check command availability
            try:
                pong = self.adb_shell(['echo', 'pong'])
            except Exception as e:
                logger.info(e)
                continue
            show_ping(pong)

            # Check system boot completed
            # MuMu 的 adb 是 vbox NAT 转发，虚机进程一起来端口就通，`pm list packages`
            # 也只依赖 package manager，两者都比 boot 完成早得多。不看 sys.boot_completed
            # 的话，launch 后 10 秒就会被判成「就绪」，随后 nemu_ipc / atx-agent 全连不上。
            try:
                boot = self.adb_shell(['getprop', 'sys.boot_completed']).strip()
            except Exception as e:
                logger.info(e)
                continue
            # 还没 boot 完时 getprop 返回空字符串，不是 '0'
            if boot != '1':
                show_boot_wait(boot)
                continue

            # Check azuelane package
            packages = self.list_known_packages(show_log=False)
            if len(packages):
                pass
            else:
                continue
            show_package(packages)

            # All check passed
            break

        if new_window != 0 and new_window != current_window:
            if window_hider is not None:
                # Minimizing leaves an icon in the taskbar, hiding does not.
                window_hider.finish()
            else:
                logger.info(f'Minimize new window: {new_window}')
                minimize_window(new_window)
        if current_window:
            logger.info(f'De-flash current window: {current_window}')
            flash_window(current_window, flash=False)
        if new_window:
            logger.info(f'Flash new window: {new_window}')
            flash_window(new_window, flash=True)
        logger.info('Emulator start completed')
        return True

    def _create_window_hider(self):
        """
        Create a window hider for MuMu Player 12 when the user enabled it.

        Returns:
            MuMuWindowHider | None: Hider instance, or None if not applicable
        """
        if not self.config.Emulator_HideWindowOnStart:
            logger.info('Emulator window hiding skipped: disabled in config')
            return None
        instance = self.emulator_instance
        if instance is None:
            logger.info('Emulator window hiding skipped: no emulator instance found')
            return None
        if instance != Emulator.MuMuPlayer12:
            logger.info(f'Emulator window hiding skipped: {instance.type} is not MuMu Player 12')
            return None
        if instance.MuMuPlayer12_id is None:
            logger.info(f'Emulator window hiding skipped: cannot get instance index from {instance.name}')
            return None
        if not instance.name:
            logger.info('Emulator window hiding skipped: empty instance name')
            return None
        console = Emulator.single_to_console(instance.emulator.path)
        if not os.path.exists(console):
            logger.info(f'Emulator window hiding skipped: console not found {console}')
            return None
        return MuMuWindowHider(
            console_exe=console,
            instance_id=instance.MuMuPlayer12_id,
            instance_name=instance.name,
        )

    def emulator_start(self):
        logger.hr('Emulator start', level=1)
        # Capture before the launch command: with synchronous MuMuManager calls
        # the emulator may already own the foreground when the watch starts, and
        # "focus back" would then target the emulator window instead of the user.
        current_window = get_focused_window()
        hider = self._create_window_hider()
        if hider is not None:
            hider.start()
        # Always stop the hider, including failure and timeout paths, otherwise
        # it keeps pressing the window down and breaks the user's boss key.
        try:
            for _ in range(3):
                # Stop
                if not self._emulator_function_wrapper(self._emulator_stop):
                    return False
                # Start
                if self._emulator_function_wrapper(self._emulator_start):
                    if self.emulator_start_watch(window_hider=hider, current_window=current_window):
                        # Success
                        return True
                    else:
                        # Start command was sent but emulator didn't come online, stop and start again
                        continue
                else:
                    # Failed to start, stop and start again
                    if self._emulator_function_wrapper(self._emulator_stop):
                        continue
                    else:
                        return False

            logger.error('Failed to start emulator 3 times, stopped')
            return False
        finally:
            if hider is not None:
                hider.stop()

    def emulator_stop(self):
        logger.hr('Emulator stop', level=1)
        for _ in range(3):
            # Stop
            if self._emulator_function_wrapper(self._emulator_stop):
                # Success
                return True
            else:
                # Failed to stop, start and stop again
                if self._emulator_function_wrapper(self._emulator_start):
                    continue
                else:
                    return False

        logger.error('Failed to stop emulator 3 times, stopped')
        return False
    
if __name__ == '__main__':
    self = PlatformWindows('alas')
    d = self.emulator_instance
    print(d)