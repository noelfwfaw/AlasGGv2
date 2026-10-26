"""MAA 侧的 MuMu 窗口隐藏器装配。

隐藏逻辑全部复用 ALAS 核心库的 `MuMuWindowHider`，本文件只做三件 MAA 特有的事：
1. 用 MAA 拿到的 `EmulatorInstance` 反推控制台路径、实例编号与别名；
2. 关闭 pid 基线（MAA 只拉起「当前不可达」的那一个实例，其余实例可能本来就在运行，
   用基线之外一律算目标会把别人的窗口一起隐藏）；
3. 把不支持的平台静默降级为「不隐藏」，不影响 MAA 自己的启动流程。
"""
import os

from module.device.platform.emulator_windows import Emulator
from module.device.platform.mumu_window_hider import MuMuWindowHider
from module.logger import logger


class MaaWindowHider(MuMuWindowHider):
    """
    供 MAA 使用的隐藏器。

    MAA 不走 ALAS 的 `emulator_start_watch()`，收尾时机由 `MaaEmulator` 自己控制：
    adb 上线且 boot 完成后调 `finish()`，再调 `stop()`。
    """

    @classmethod
    def create(cls, instance):
        """
        Args:
            instance (EmulatorInstanceBase): 从模拟器列表里取到的实例

        Returns:
            MaaWindowHider | None: 无法隐藏时返回 None
        """
        if instance != Emulator.MuMuPlayer12:
            logger.info(f'MAA window hiding skipped: {instance.type} is not MuMu Player 12')
            return None
        if instance.MuMuPlayer12_id is None:
            logger.info(f'MAA window hiding skipped: cannot get instance index from {instance.name}')
            return None
        if not instance.name:
            logger.info('MAA window hiding skipped: empty instance name')
            return None
        console = Emulator.single_to_console(instance.emulator.path)
        if not os.path.exists(console):
            logger.info(f'MAA window hiding skipped: console not found {console}')
            return None
        return cls(
            console_exe=console,
            instance_id=instance.MuMuPlayer12_id,
            instance_name=instance.name,
        )

    def start(self, allow_baseline=False) -> None:
        """MAA 默认不用 pid 基线，识别依赖 `MuMuManager info` 的 pid 与别名。"""
        super().start(allow_baseline=allow_baseline)
