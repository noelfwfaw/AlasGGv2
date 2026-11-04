from module.gg_handler.gg_data import GGData
from module.gg_handler.gg_u2 import GGU2
# from module.gg_handler.gg_screenshot import GGScreenshot
from module.logger import logger
from module.notify import handle_notify_throttled
from module.base.timer import timeout
import module.config.utils as utils

# 导入 time 模块的函数
from time import sleep, time

# 如果 utils 模块中没有 deep_get 和 deep_set，则动态定义并注入
if not (hasattr(utils, 'deep_get') and hasattr(utils, 'deep_set')):
    def deep_get(d, keys, default=None):
        """
        递归安全地获取嵌套字典的值
        """
        if isinstance(keys, str):
            keys = keys.split('.')
        assert isinstance(keys, list)
        if d is None:
            return default
        if not keys:
            return d
        return deep_get(d.get(keys[0]), keys[1:], default)

    def deep_set(d, keys, value):
        """
        递归安全地设置嵌套字典的值
        """
        if isinstance(keys, str):
            keys = keys.split('.')
        assert isinstance(keys, list)
        if not keys:
            return value
        if not isinstance(d, dict):
            d = {}
        d[keys[0]] = deep_set(d.get(keys[0], {}), keys[1:], value)
        return d

    # 将定义好的函数添加到 utils 模块中
    utils.deep_get = deep_get
    utils.deep_set = deep_set

# 然后再从 utils 模块导入 deep_get 和 deep_set
from module.config.utils import deep_get, deep_set

# Maximum attempts of restarting the game before requesting human takeover.
MAX_RESTART_ATTEMPT = 3

# GG 启用前置闸：等待游戏完全进入的最长总时长（秒）。
GG_LOAD_TIMEOUT = 600
# GG 启用前置闸：已知界面上需连续保持稳定的时长（秒）。
# 日服客户端在登录确认后 30~90 秒内可能回跳标题页（网络抖动），必须等过这个窗口。
GG_HOLD_SECONDS = 90
# GG 启用后置闸：确认游戏回到已加载界面的最长等待（秒）。
GG_VERIFY_SECONDS = 120


class GGHandler:
    """
    A module to handle needs of cheaters
    Args:
        config: AzurlaneConfig
        device: Device
    """

    def __init__(self, config=None, device=None):
        self.config = config
        self.device = device
        self.factor = deep_get(self.config.data,
                               'GameManager.GGHandler.GGMultiplyingFactor',
                               default=200)
        self.method = deep_get(self.config.data,
                               'GameManager.GGHandler.GGMethod',
                               default='screenshot')

    def restart(self, crashed=False):
        from module.handler.login import LoginHandler
        from module.exception import GameStuckError, RequestHumanTakeover
        _crashed = crashed
        attempt_count = 0
        while 1:
            try:
                if timeout(LoginHandler(config=self.config, device=self.device).app_restart, timeout_sec=600):
                    logger.info(f'Game restarted successfully after {attempt_count + 1} attempts.')
                    return
                logger.warning(f'App restart timeout on attempt {attempt_count + 1}')
            except GameStuckError:
                raise
            except Exception as e:
                logger.exception(e)
            attempt_count += 1
            if attempt_count >= MAX_RESTART_ATTEMPT:
                raise RequestHumanTakeover(f'Game restart failed after {MAX_RESTART_ATTEMPT} attempts')
            self.device.stuck_record_clear()

    def _ui(self):
        """构造一个轻量 UI 实例用于页面识别（懒加载避免循环导入）。"""
        from module.ui.ui import UI
        return UI(config=self.config, device=self.device)

    def _wait_game_loaded(self):
        """
        GG 启用前置闸：游戏必须完全进入并稳定后才允许启动 GG。

        - 游戏未启动：倍率补丁打在旧进程上也活不过随后的启动，按
          「游戏重启后 GG 必须重新启用、无法沿用」的规则，直接报错走
          调度循环的恢复（重启游戏）路径，绝不在游戏未运行时启用 GG。
        - 登录刚确认的 30~90 秒内，日服客户端可能回跳标题页（网络抖动），
          此时启动 GG 会把倍率打进未就绪/即将重载的内存，倍率丢失 → 裸奔
          战败（2026-11-04 实例 D 15-1）。这里等待已知界面出现，并要求其
          连续稳定 GG_HOLD_SECONDS 秒；期间任何弹窗/回跳都会重新观察。
        - 待机画面（客户端 ~60 秒无操作后进入）本身就是「游戏已完全加载且
          稳定闲置 60 秒」的证明，视为稳定直接放行，不点击返回（点击会把
          REWARD_GOTO_MAIN 累积进点击记录，多轮观察后触发
          GameTooManyClickError，2026-11-04 10:50 实测）。
        - 超时抛 GameStuckError：宁可报错走恢复/停止，也不能裸奔。
        """
        from module.exception import (
            GameNotRunningError,
            GamePageUnknownError,
            GameStuckError,
            GameTooManyClickError,
        )
        from module.ui.assets import IDLE
        ui = self._ui()
        deadline = time() + GG_LOAD_TIMEOUT
        while 1:
            try:
                page = ui.ui_get_current_page()
            except GameNotRunningError:
                logger.critical('Game not running before GG enable, refuse to enable GG')
                raise GameStuckError('GameNotRunning before GG enable')
            except (GamePageUnknownError, GameTooManyClickError):
                self.device.stuck_record_clear()
                if time() > deadline:
                    logger.critical('Game not fully loaded before GG enable (timeout)')
                    raise GameStuckError('Game not fully loaded for GG enable')
                continue
            if time() > deadline:
                raise GameStuckError('Game not fully loaded for GG enable')
            logger.info(f'Game page known ({page.name}), '
                        f'hold {GG_HOLD_SECONDS}s to confirm game is stable')
            hold_start = time()
            broken = False
            while time() - hold_start < GG_HOLD_SECONDS:
                self.device.screenshot()
                self.device.stuck_record_clear()
                if time() > deadline:
                    raise GameStuckError('Game not fully loaded for GG enable')
                if not ui.ui_page_appear(page):
                    if IDLE.match_luma(self.device.image, offset=(5, 5)):
                        # 待机画面：游戏加载完成后闲置 60 秒的产物，视作稳定证明
                        logger.info('Idle screen detected, game is fully loaded and stable')
                        return
                    # 弹窗遮挡/回跳标题/界面切换：稳定性被打破，重新观察
                    broken = True
                    break
            if not broken:
                logger.info('Game fully loaded and stable, continue to enable GG')
                return

    def _verify_after_enable(self):
        """
        GG 启用后置闸：启用流程期间游戏在后台 25~45 秒，若游戏在此期间死亡重启
        或回跳登录页，倍率补丁必然丢失。这里确认游戏回到已加载界面且未出现登录页；
        一旦发现登录页或长时间停在未知界面，判定倍率可疑：置 gg_on=False 并抛错，
        由调度循环的 GameStuckError 恢复路径重新走一次「等待加载 → 启用 GG」。
        """
        from module.exception import GamePageUnknownError, GameStuckError, GameTooManyClickError
        from module.handler.assets import LOGIN_CHECK
        ui = self._ui()
        deadline = time() + GG_VERIFY_SECONDS
        while 1:
            self.device.screenshot()
            self.device.stuck_record_clear()
            if ui.appear(LOGIN_CHECK, offset=(30, 30)):
                logger.warning('Login screen appeared after GG enable, '
                               'multiplier is lost, need re-enable')
                GGData(self.config).set_data(target='gg_on', value=False)
                raise GameStuckError('Game back to login after GG enable')
            try:
                page = ui.ui_get_current_page()
            except (GamePageUnknownError, GameTooManyClickError):
                if time() > deadline:
                    logger.warning('Unknown page after GG enable, '
                                   'multiplier is uncertain, need re-enable')
                    GGData(self.config).set_data(target='gg_on', value=False)
                    raise GameStuckError('Unknown page after GG enable')
                continue
            logger.info(f'Game back to known page ({page.name}) after GG enable')
            return

    def set(self, mode=True):
        """
            Set the GG status to True/False.
            Args:
                mode: bool
        """
        gg_package_name = deep_get(self.config.data, keys='GameManager.GGHandler.GGPackageName')
        if mode:
            logger.hr('Enabling GG')
            self.handle_u2_restart()
            # 前置闸：游戏未完全进入并稳定，绝不启动 GG
            self._wait_game_loaded()
            success = timeout(GGU2(config=self.config, device=self.device).set_on, timeout_sec=deep_get(self.config.data, "GameManager.GGHandler.Timeout"), factor=self.factor)
            if not success:
                from module.exception import GameStuckError
                raise GameStuckError('GG enable timeout')
            logger.info(f'GG enabled, factor={self.factor}')
            # 后置闸：启用期间游戏在后台，死亡/回跳登录则倍率已丢
            self._verify_after_enable()
            # Not support screenshot anymore
        else:
            self.gg_reset()

    def skip_error(self) -> bool:
        """
        Close all the windows of GG.
        Often to be used when game restarts with GG enabled.
        Returns:
            bool: Whether GG error panel occurs
        """
        gg_package_name = deep_get(self.config.data, keys='GameManager.GGHandler.GGPackageName')
        # if self.method == 'screenshot' or gg_package_name == 'com.':
        #     return \
        #         GGScreenshot(config=self.config, device=self.device).skip_error()
        # elif self.method == 'u2':
        #     return \
        #         GGU2(config=self.config, device=self.device).skip_error()
        return GGU2(config=self.config, device=self.device).skip_error()  # Not support screenshot anymore

    def check_config(self) -> dict:
        """
        Reset GG config to the user's config and return gg_data.
        Returns:
            gg_data: dict = {
                        'gg_enable' : bool = Whether GG manager enabled,
                        'gg_auto' : bool = Whether to start GG before tasks,
                        'gg_on' : bool = Whether multiplier is on now}
        """
        gg_enable = deep_get(d=self.config.data, keys='GameManager.GGHandler.Enabled', default=False)
        gg_auto = deep_get(d=self.config.data, keys='GameManager.GGHandler.AutoRestartGG', default=False)
        GGData(self.config).set_data(target='gg_enable', value=gg_enable)
        GGData(self.config).set_data(target='gg_auto', value=gg_auto)
        gg_data = GGData(self.config).get_data()
        logger.info(f'GG status:')
        logger.info(
            f'Enabled={gg_data["gg_enable"]} AutoRestart={gg_data["gg_auto"]} Current stage={gg_data["gg_on"]}')
        return gg_data

    def handle_u2_restart(self):
        _need_restart_atx = deep_get(d=self.config.data, keys='GameManager.GGHandler.RestartATX')
        if _need_restart_atx:
            try:
                if not timeout(self.device.restart_atx, 60):
                    logger.warning('Restart ATX timeout, trying to restart emulator')
                    self.device.emulator_start()
            except Exception as e:
                logger.exception(e)
                logger.critical('Maybe your emulator died, trying to restart it')
                handle_notify_throttled(
                    self.config.Error_OnePushConfig,
                    key='gg_emulator_died',
                    title=f"Alas <{self.config.config_name}> emulator error",
                    content=f"<{self.config.config_name}> GG ATX/uiautomator died, restarting emulator",
                )
                self.device.emulator_start()
            import uiautomator2 as u2
            logger.info('Reset UiAutomator')
            try:
                u2.connect(self.device.serial).reset_uiautomator()
            except Exception as e:
                logger.exception(e)
                logger.critical('Maybe your emulator died, trying to restart it')
                handle_notify_throttled(
                    self.config.Error_OnePushConfig,
                    key='gg_emulator_died',
                    title=f"Alas <{self.config.config_name}> emulator error",
                    content=f"<{self.config.config_name}> GG ATX/uiautomator died, restarting emulator",
                )
                self.device.emulator_start()

    def handle_restart(self):
        """
        Handle the restart errors of GG.
        """
        gg_data = GGData(config=self.config).get_data()
        gg_enable = gg_data['gg_enable']
        if gg_enable:
            GGData(config=self.config).set_data(target='gg_on', value=False)
            logger.info(f'GG status:')
            logger.info(
                f'Enabled={gg_data["gg_enable"]} AutoRestart={gg_data["gg_auto"]} Current stage={gg_data["gg_on"]}')
            if not self.skip_error():
                logger.hr('Assume game died without GG panel')

    def gg_reset(self):
        """
        Force restart the game to reset GG status to False
        """
        gg_data = GGData(self.config).get_data()
        if gg_data['gg_enable'] and gg_data['gg_on']:
            logger.hr('Disabling GG')
            self.restart()
            logger.attr('GG', 'Disabled')

    def check_status(self, mode=True):
        """
        A check before a task begins to decide whether to enable GG and set it.
        Args:
            mode: The multiplier status when finish the check.
        """
        gg_data = GGData(self.config).get_data()
        if gg_data['gg_enable']:
            gg_auto = mode if deep_get(d=self.config.data,
                                       keys='GameManager.GGHandler.AutoRestartGG',
                                       default=False) else False
            logger.info(f'Check GG status:')
            logger.info(
                f'Enabled={gg_data["gg_enable"]} AutoRestart={gg_data["gg_auto"]} Current stage={gg_data["gg_on"]}')
            if gg_auto:
                if not gg_data['gg_on']:
                    self.set(True)
            elif gg_data['gg_on']:
                self.gg_reset()

    def power_limit(self, task=''):
        """
        Forced final check before some dangerous tasks for cheaters.
        If power is too high, disable the multiplier and assume the user need GG to be Enabled before the other tasks.
        Args:
            task: str = What task it is to limit power, default limit is 17000 for front ships.
        """
        from module.gg_handler.assets import OCR_PRE_BATTLE_CHECK
        from module.ocr.ocr import Digit
        self.device.screenshot()
        OCR_CHECK = Digit(OCR_PRE_BATTLE_CHECK, letter=(255, 255, 255), threshold=128)
        ocr = OCR_CHECK.ocr(self.device.image)
        from module.config.utils import deep_get
        limit = deep_get(self.config.data, keys=f'GameManager.PowerLimit.{task}', default=17000)
        logger.attr('Power Limit', limit)
        if ocr >= limit:
            logger.critical('There''s high chance that GG is on, restart to disable it')
            from module.gg_handler.gg_data import GGData
            GGData(self.config).set_data(target='gg_on', value=False)
            GGData(self.config).set_data(target='gg_enable', value=True)
            deep_set(d=self.config.data, keys='GameManager.GGHandler.Enabled', value=True)
            deep_set(d=self.config.data, keys='GameManager.GGHandler.AutoRestartGG', value=True)
            self.config.task_call('Restart')
            self.config.task_delay(minute=0.5)
            self.config.task_stop('Restart for sake of safty')

    def handle_restart_before_tasks(self) -> bool:
        """
        Check if user need to restart everytime alas starts before tasks, and handle it.
        Returns:
            bool: If it needs restart first
        """
        gg_data = GGData(self.config).get_data()
        if (deep_get(d=self.config.data,
                     keys='GameManager.GGHandler.RestartEverytime',
                     default=True)
                and gg_data['gg_enable']):
            logger.info('Restart to reset GG status.')
            self.restart()
            return True
        return False

    def check_then_set_gg_status(self, task=''):
        """
        If task is in list _disabled or _group_enabled defined in this function,
        set gg to the defined status
        Args:
            task : str = the next task to run
        """
        _disabled_task = deep_get(self.config.data, 'GameManager.GGHandler.DisabledTask')
        """
            'disable_all_dangerous_task'
            'disable_meta_and_exercise'
            'disable_exercise'
            'enable_all'
        """
        _disabled = [
            'minigame'
        ]
        _group_exercise = [
            'exercise'
        ]
        _group_meta = [
            'opsi_ash_assist',
            'opsi_ash_beacon'
        ]
        _group_raid = [
            'raid',
            'raid_daily',
            'coalition',
            'coalition_sp'
        ]
        _group_personal_choice = [
            'guild'
        ]
        _group_enabled = [
            'hard',
            'sos',
            'war_archives',
            'event_a',
            'event_b',
            'event_c',
            'event_d',
            'event_sp',
            'maritime_escort',
            'opsi_explore',
            'opsi_daily',
            'opsi_obscure',
            'opsi_month_boss',
            'opsi_abyssal',
            'opsi_archive',
            'opsi_stronghold',
            'opsi_meowfficer_farming',
            'opsi_hazard1_leveling',
            'opsi_cross_month',
            'main',
            'main2',
            'main3',
            'event',
            'event2',
            'event3',
            'gems_farming',
            'c72_mystery_farming',
            'c122_medium_leveling',
            'c124_large_leveling',
        ]

        # Handle ignorance

        if _disabled_task == 'disable_meta_and_exercise':
            _disabled += _group_exercise + _group_meta
            _enabled = _group_enabled + _group_raid + _group_personal_choice
        elif _disabled_task == 'disable_exercise':
            _disabled += _group_exercise
            _enabled = _group_enabled + _group_personal_choice + _group_raid + _group_meta
        elif _disabled_task == 'enable_all':
            _enabled = _group_enabled + _group_personal_choice + _group_raid + _group_meta + _group_exercise
            # _disabled = []
        elif _disabled_task == 'disable_guild_and_dangerous':
            _disabled += _group_exercise + _group_meta + _group_raid + _group_personal_choice
            _enabled = _group_enabled
        else:  # _disabled_task == 'disable_all_dangerous_task':
            _disabled += _group_exercise + _group_meta + _group_raid
            _enabled = _group_enabled + _group_personal_choice

        if task in _disabled:
            self.check_status(False)
        elif task in _enabled:
            self.check_status(True)