import os

from adbutils import AdbClient

from module.base.timer import Timer
from module.logger import logger


def emulator_start_instance(instance):
    """
    向模拟器发出启动命令，不等待。

    只借用 PlatformWindows 上的两个 classmethod，不构造 Platform 实例——
    Platform 继承 Connection，构造时会去 adb_connect 目标设备，
    模拟器没开时会直接抛 EmulatorNotRunningError，正是这里要处理的场景。

    平台相关模块延迟导入：emulator_windows 依赖 winreg，非 Windows 上不可 import。
    """
    from module.device.platform.platform_windows import PlatformWindows
    PlatformWindows._emulator_start(PlatformWindows, instance)


def emulator_stop_instance(instance):
    """向模拟器发出关闭命令，不等待。"""
    from module.device.platform.platform_windows import PlatformWindows
    PlatformWindows._emulator_stop(PlatformWindows, instance)


class MaaEmulator:
    """
    MAA 插件专用的模拟器控制器。

    不复用 ALAS 的 Device / Platform，原因有两个：
    1. Device.list_device() 带 @retry，失败会走 adb_reconnect()，那里面还有一路
       detect_device() + check_mumu_bridge_network()，模拟器不在时能空转好几秒；
    2. Platform._emulator_start() 内部还有一次「先等占位文件消失」的逻辑，
       MuMu 正常启动时也要白等 20 秒。

    这里只保留必要的 adb 探测与启停，全程同步、无重试、日志可控。
    """

    @staticmethod
    def adb_client():
        host = '127.0.0.1'
        port = 5037

        env = os.environ.get('ANDROID_ADB_SERVER_PORT', None)
        if env is not None:
            try:
                port = int(env)
            except ValueError:
                pass

        return AdbClient(host, port)

    def device_serials(self):
        """
        adb 设备列表里状态为 device 的 serial。

        adbutils 0.11 的 `device_list()` 只返回状态为 device 的设备，也没有 `.status` 属性，
        不同版本行为不一致，所以这里对 status 做兼容判断。

        Returns:
            list[str]:
        """
        try:
            devices = self.adb_client().device_list()
            serials = []
            for device in devices:
                status = getattr(device, 'status', None)
                if status is not None and status != 'device':
                    continue
                serials.append(device.serial)
            return serials
        except Exception as e:
            logger.info(e)
            return []

    def alive(self, serial):
        """
        模拟器是否在线：serial 在 adb 列表中即为在线（不再额外检查状态字符串）。

        正常情况只有一次 `adb devices`（约 30ms）；不在列表时补一次 adb connect 复核，
        避免 adb 缓存抖动造成的误判。

        Returns:
            bool:
        """
        if serial in self.device_serials():
            return True

        try:
            msg = self.adb_client().connect(serial)
            logger.info(msg)
        except Exception as e:
            logger.info(e)
            return False
        return serial in self.device_serials()

    def find(self, serial):
        """
        按 serial 查找模拟器实例。

        找不到时再按 MuMu 实例编号兜底（MuMu 重启后 adb 端口会漂移）。

        Returns:
            EmulatorInstanceBase | None:
        """
        try:
            from module.device.platform.emulator_windows import EmulatorManager
            instances = EmulatorManager().all_emulator_instances
        except Exception as e:
            logger.info(e)
            return None

        for instance in instances:
            if instance.serial == serial:
                return instance

        from module.device.platform.emulator_windows import Emulator
        from module.device.platform.platform_base import serial_to_id
        instance_id = serial_to_id(serial)
        if instance_id is not None:
            for instance in instances:
                if instance == Emulator.MuMuPlayer12 and instance.MuMuPlayer12_id == instance_id:
                    return instance

        return None

    def start(self, instance):
        """
        启动模拟器（只发启动命令，不等待）。调用前必须先用 alive()/find() 确认它真的不在。

        Returns:
            bool: 是否成功发出启动命令
        """
        if self._mumu_id_missing(instance):
            return False
        logger.info(f'Start emulator: {instance.name}')
        try:
            emulator_start_instance(instance)
        except Exception as e:
            logger.exception(e)
            return False
        return True

    def stop(self, instance):
        """
        关闭模拟器（只发关闭命令，不等待）。

        Returns:
            bool: 是否成功发出关闭命令
        """
        if self._mumu_id_missing(instance):
            return False
        logger.info(f'Stop emulator: {instance.name}')
        try:
            emulator_stop_instance(instance)
        except Exception as e:
            logger.exception(e)
            return False
        return True

    @staticmethod
    def _mumu_id_missing(instance):
        """
        MuMu 的启停命令必须带实例编号，编号解析失败时命令会作用到错误的目标上。
        """
        from module.device.platform.emulator_windows import Emulator
        if instance == Emulator.MuMuPlayer12 and instance.MuMuPlayer12_id is None:
            logger.critical(
                f'无法从实例名 {instance.name} 解析 MuMu 实例编号，已跳过启停操作'
            )
            return True
        return False

    def boot_completed(self, serial):
        try:
            return self.adb_client().shell(serial, 'getprop sys.boot_completed').strip() == '1'
        except Exception:
            return False

    @staticmethod
    def shell(serial, command, timeout=2):
        """
        在设备上执行一条 adb shell 命令。

        AdbClient.shell 不检查退出码，`pidof` 找不到进程时只会返回空字符串而不是抛异常；
        设备离线、adb 超时才会抛异常，调用方需要自己区分这两种情况。

        Returns:
            str: 命令输出
        """
        return MaaEmulator.adb_client().shell(serial, command, timeout=timeout)

    def wait_online(self, instance, timeout=180):
        """
        等待模拟器真的可用：adb 上线且系统启动完成。

        MuMu 重启后 adb 端口会漂移，所以返回的 serial 可能和实例配置里的不同。

        Returns:
            str | None: 可用的 serial，超时返回 None
        """
        serial = instance.serial
        from module.device.platform.emulator_windows import Emulator
        from module.device.platform.platform_base import serial_to_id

        instance_id = None
        if instance == Emulator.MuMuPlayer12:
            instance_id = instance.MuMuPlayer12_id

        timer = Timer(timeout).start()
        interval = Timer(1).start()
        while 1:
            serials = self.device_serials()
            online = serial if serial in serials else None
            if online is None and instance_id is not None:
                for candidate in serials:
                    if serial_to_id(candidate) == instance_id:
                        online = candidate
                        break

            if online is not None and self.boot_completed(online):
                if online != serial:
                    logger.info(f'Emulator serial changed: {serial} -> {online}')
                logger.info(f'Emulator online: {online}')
                return online

            if timer.reached():
                logger.warning(f'Emulator not online in {timeout}s: {instance.name}')
                return None

            interval.wait()
            interval.reset()

    def wait_offline(self, serial, timeout=30):
        """
        等待 serial 从 adb 设备列表消失。

        Returns:
            bool: 是否已消失（超时返回 False）
        """
        timer = Timer(timeout).start()
        interval = Timer(1).start()
        while 1:
            if serial not in self.device_serials():
                logger.info(f'Emulator stopped: {serial}')
                return True

            if timer.reached():
                logger.warning(f'Emulator still online after {timeout}s: {serial}')
                return False

            interval.wait()
            interval.reset()

    @staticmethod
    def is_windows():
        from module.device.env import IS_WINDOWS
        return IS_WINDOWS
