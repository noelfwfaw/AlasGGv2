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

# GG 启用后置闸：启用后观察游戏是否还活着的时长（秒）。
# GG 会把游戏压到后台 25~45 秒，这期间游戏可能重载/掉线，倍率补丁随之丢失。
GG_VERIFY_SECONDS = 45

# GG 启用前置闸：同一个游戏内页面要连续稳定这么久（秒），才算「游戏启动完成」。
# 单独一帧匹配到 page_main 就放行是不够的——从登录画面点进主界面、或刚打完
# 一局的那一瞬间都会匹配到，此时启动还没走完。
GG_GATE_SETTLE_SECONDS = 3

# GG 启用前置闸的最长观察时间（秒）。超时即拒绝，交给调度重启游戏，绝不在
# 启动过程中硬拉起 GG。正常的加载过渡都在 20 秒内，等不到就没必要再等。
GG_GATE_TIMEOUT = 30


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

    def _detect_page(self, ui):
        """只做识别，不点任何东西。

        Returns:
            Page or None: 认不出时返回 None（登录页、加载页、公告、战斗中…）
        """
        from module.ui.page import Page
        for page in Page.iter_pages():
            if page.check_button is None:
                continue
            if ui.ui_page_appear(page=page):
                return page
        return None

    def _wait_game_loaded(self):
        """
        GG 启用前置闸：确认「游戏已经启动完成」，而不是还在启动/登录过程中。

        绝不能走 ui_get_current_page()：它的 ui_additional() 里有一条
        `appear_then_click(LOGIN_CHECK)`（module/ui/ui.py:559），也就是说前置闸
        自己会把标题画面点掉、替游戏把登录走完，然后紧接着拉起 GG。于是形成
        「ALAS 帮游戏启动 → 立刻 GG → GG 把刚登录的客户端压到后台 20~40 秒 →
        日服客户端回来又掉回标题 → 倍率丢失 → 重试」的死循环（2026-11-06
        01:50~01:53 实例 D 连开三次 GG，每次都发生在游戏正在启动的时候）。
        所以这里只观察、不点击，看到登录页直接拒绝，交给调度走 Restart 重新登录。

        判定标准：同一个游戏内页面连续稳定 GG_GATE_SETTLE_SECONDS 秒。单独一帧
        匹配到 page_main 就放行是不够的——从登录画面刚点进来的那一瞬间也会匹配。

        待机画面（日服 60 秒无操作会进）仍然要点回主界面，否则永远等不到稳定页面；
        但点完要重新计时，不能把「待机 → 主界面」当成启动完成。
        """
        from module.exception import GameStuckError
        from module.handler.assets import LOGIN_CHECK

        ui = self._ui()
        logger.info('Waiting for the game to finish loading before enabling GG')
        if not ui.device.app_is_running():
            logger.critical('Game not running before GG enable, refuse to enable GG')
            raise GameStuckError('GameNotRunning before GG enable')

        deadline = time() + GG_GATE_TIMEOUT
        settled_page = None
        settled_since = None
        while time() < deadline:
            ui.device.screenshot()
            ui.device.stuck_record_clear()

            # 登录页：游戏正在启动/掉线重连。绝不在这里替它点登录。
            if ui.appear(LOGIN_CHECK, offset=(30, 30)):
                logger.critical('Game is at the login screen, '
                                'refuse to enable GG during startup')
                raise GameStuckError('Game at login before GG enable')

            # 待机画面：点回主界面后重新计时
            if ui.handle_idle_page():
                settled_page = None
                settled_since = None
                ui.device.sleep(0.5)
                continue

            page = self._detect_page(ui)
            if page is None:
                if settled_page is not None:
                    logger.info(f'Game left {settled_page.name}, restart counting')
                settled_page = None
                settled_since = None
                ui.device.sleep(0.5)
                continue

            if settled_page is None or page.name != settled_page.name:
                if settled_page is not None:
                    logger.info(f'Game left {settled_page.name}, restart counting')
                settled_page = page
                settled_since = time()
                ui.device.sleep(0.5)
                continue

            if time() - settled_since >= GG_GATE_SETTLE_SECONDS:
                logger.info(f'Game is in {page.name} and has been stable for '
                            f'{GG_GATE_SETTLE_SECONDS}s, continue to enable GG')
                return
            # 观察窗口内的节流等待，不是状态循环里的点击等待
            ui.device.sleep(0.5)

        logger.critical(f'Game did not settle within {GG_GATE_TIMEOUT}s, '
                        'refuse to enable GG')
        raise GameStuckError('Game not loaded before GG enable')

    def _verify_after_enable(self):
        """
        GG 启用后置闸：启用后连续观察 GG_VERIFY_SECONDS 秒，确认游戏还活着。

        GG 把游戏压到后台 25~45 秒，这期间游戏重载/掉线，倍率补丁必然丢失，
        而 GGData 里 gg_on 仍是 True —— 接下来整轮任务都在裸奔（2026-11-04 上午
        实例 D 战败即此）。所以启用后必须盯一段固定时间：期间出现登录页或游戏
        进程消失，就置 gg_on=False 并抛 GameStuckError，让调度走重启恢复，
        宁可报错停下也不裸奔。

        这里只查「登录页」和「进程是否还在」，不做页面识别，也不因为页面变化
        重置计时——待机画面、弹窗、界面切换都不改变「游戏还活着」这个事实，
        只有登录页/进程消失才说明倍率随内存丢了。
        """
        from module.exception import GameStuckError
        from module.handler.assets import LOGIN_CHECK
        ui = self._ui()
        logger.info(f'Verifying GG for {GG_VERIFY_SECONDS}s')
        deadline = time() + GG_VERIFY_SECONDS
        while time() < deadline:
            self.device.screenshot()
            self.device.stuck_record_clear()
            if ui.appear(LOGIN_CHECK, offset=(30, 30)):
                logger.warning('Login screen appeared after GG enable, '
                               'multiplier is lost, need re-enable')
                GGData(self.config).set_data(target='gg_on', value=False)
                raise GameStuckError('Game back to login after GG enable')
            try:
                running = self.device.app_is_running()
            except Exception as e:
                logger.warning(f'Failed to check app status: {e}')
                running = True
            if not running:
                logger.warning('Game is gone after GG enable, '
                               'multiplier is lost, need re-enable')
                GGData(self.config).set_data(target='gg_on', value=False)
                raise GameStuckError('Game died after GG enable')
            # 固定观察窗口内的节流等待，不是状态循环里的点击等待
            self.device.sleep(1)
        logger.info(f'GG verified, game is alive after {GG_VERIFY_SECONDS}s')

    def set(self, mode=True):
        """
            Set the GG status to True/False.
            Args:
                mode: bool
        """
        gg_package_name = deep_get(self.config.data, keys='GameManager.GGHandler.GGPackageName')
        if mode:
            self.handle_u2_restart()
            # 前置闸：游戏没进入稳定页面就绝不启动 GG。放在横幅之前——被闸拦下的
            # 这一轮不该在日志里表现成「又启用了一次 GG」。
            self._wait_game_loaded()
            logger.hr('Enabling GG')
            success = timeout(GGU2(config=self.config, device=self.device).set_on, timeout_sec=deep_get(self.config.data, "GameManager.GGHandler.Timeout"), factor=self.factor)
            if not success:
                from module.exception import GameStuckError
                raise GameStuckError('GG enable timeout')
            logger.info(f'GG enabled, factor={self.factor}')
            # 后置闸：启用期间游戏在后台，重载/掉线则倍率已丢
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
            # 大世界半自动会实际进入战斗，必须和手动操作时保持同一倍率。
            # 之前没归类，check_then_set_gg_status() 对它是空操作，GG 状态
            # 完全取决于上一个任务遗留，容易出现整段裸奔。
            'opsi_daemon',
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