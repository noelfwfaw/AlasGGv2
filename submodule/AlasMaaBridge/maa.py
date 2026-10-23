import os
import json
import ctypes
from datetime import datetime

from cached_property import cached_property

# In order for MAA submodule to exexute,
# it is necessary to load runtime before module PIL
if os.name == 'nt':
    # This DLL is the dependency for next DLL
    # Try loading to avoid mix different versions of runtime
    try:
        ctypes.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32/vcruntime140_1.dll'))
    except Exception as e:
        print(e)

    # This DLL must be loaded due to conflict issues
    ctypes.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32/msvcp140.dll'))

    # These DLLS are other DLLS that MAA depends on
    # Try loading to avoid mix different versions of runtime
    try:
        ctypes.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32/msvcp140_1.dll'))
        ctypes.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32/concrt140.dll'))
    except Exception as e:
        print(e)

from alas import AzurLaneAutoScript
from module.base.decorator import del_cached_property
from module.config.config import AzurLaneConfig
from module.exception import RequestHumanTakeover
from module.logger import logger
from submodule.AlasMaaBridge.module.asst.updater import Updater as MaaUpdater
from submodule.AlasMaaBridge.module.asst.utils import Version
from submodule.AlasMaaBridge.module.config.config import ArknightsConfig
from submodule.AlasMaaBridge.module.handler.handler import AssistantHandler
from submodule.AlasMaaBridge.module.logger import log_callback


class FakeDevice:
    @staticmethod
    def empty_func(*args, **kwargs):
        pass

    def __getattr__(self, item):
        return FakeDevice.empty_func


class ArknightsAutoScript(AzurLaneAutoScript):
    @cached_property
    def device(self):
        return FakeDevice()

    @cached_property
    def config(self):
        try:
            config = ArknightsConfig(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @staticmethod
    def callback(self):
        pass

    @cached_property
    def asst(self):
        """
        跑 MAA 任务用的 MaaCore 实例。

        首次访问时会先插入一次「开始唤醒」，确保游戏已经启动。
        """
        return self.load_asst()

    def load_asst(self, check_task=True):
        """
        创建 MaaCore 实例，并完成 MaaCore 加载、回调注册与实例设置。

        Args:
            check_task (bool): 是否先检查并插入「开始唤醒」任务。
                只有任务队列清空后的等待期间才需要跳过——那时并不在任务里，
                触发 TaskEnd 会打断调度循环。

        Returns:
            Asst:
        """
        if check_task and self.config.task.command != 'Maa':
            self.config.task_call('MaaStartup', True)
            if self.config.task.command != 'MaaStartup':
                self.config.task_stop()

        # Fix MaaPath=*\MAA.exe
        if os.path.isfile(self.config.MaaEmulator_MaaPath):
            path = os.path.dirname(self.config.MaaEmulator_MaaPath)
            logger.info(f'MaaEmulator_MaaPath: {self.config.MaaEmulator_MaaPath} is revised to {path}')
            self.config.MaaEmulator_MaaPath = path

        logger.info(f'MAA安装路径：{self.config.MaaEmulator_MaaPath}')
        if not os.path.exists(self.config.MaaEmulator_MaaPath):
            logger.critical(
                f'未找到路径 {self.config.MaaEmulator_MaaPath}，请确认MAA已安装在该路径。'
                f'如果你是第一次使用MAA插件，需要自选安装MAA，并在 "MAA设置" - "MAA安装路径" 中填入MAA的安装路径')
            raise RequestHumanTakeover

        # 更新必须在加载 MaaCore 之前完成，否则 dll 被占用无法覆盖
        self.maa_update()

        try:
            incremental_path = [os.path.join(self.config.MaaEmulator_MaaPath, './cache')]
            if self.config.MaaEmulator_PackageName in ["YoStarEN", "YoStarJP", "YoStarKR", "txwy"]:
                incremental_path.append(os.path.join(
                    self.config.MaaEmulator_MaaPath,
                    './resource/global/' + self.config.MaaEmulator_PackageName)
                )
                incremental_path.append(os.path.join(
                    self.config.MaaEmulator_MaaPath,
                    './cache/resource/global/' + self.config.MaaEmulator_PackageName)
                )
            # 不存在的增量目录会让 AsstLoadResource 返回 false，进而被当成资源加载失败
            incremental_path = [p for p in incremental_path if os.path.isdir(p)]
            AssistantHandler.load(self.config.MaaEmulator_MaaPath, incremental_path)
        except ModuleNotFoundError:
            logger.critical('找不到MAA，请检查安装路径是否正确')
            raise RequestHumanTakeover
        except OSError as e:
            # OSError: [WinError 126] 找不到指定的模块。
            if '[WinError 126]' in str(e):
                logger.exception(e)
                logger.critical(
                    f'无法导入MAA，请确认MAA已正确安装在 {self.config.MaaEmulator_MaaPath}'
                )
                raise RequestHumanTakeover
            else:
                raise

        @AssistantHandler.Asst.CallBackType
        def callback(msg, details, arg):
            """
            Args:
                msg (int):
                details (bytes):
                arg (c_void_p):
            """
            d = json.loads(details.decode('utf-8', 'ignore'))
            try:
                m = AssistantHandler.Message(msg)
            except ValueError:
                # 新版 MaaCore 可能新增回调类型，未收录时不能让它把回调线程打挂
                logger.warning(f'未知的 MAA 回调类型 {msg}: {str(d)[:300]}')
                return
            log_callback(m, d)
            handler = AssistantHandler.ASST_HANDLER
            if handler:
                handler.callback_timer.reset()
                for func in handler.callback_list:
                    func(m, d)

        ArknightsAutoScript.callback = callback
        asst = AssistantHandler.Asst(callback)

        asst.set_instance_option(AssistantHandler.InstanceOptionType.touch_type, self.config.MaaEmulator_TouchMethod)
        asst.set_instance_option(AssistantHandler.InstanceOptionType.adblite_enabled, '0')
        if self.config.MaaEmulator_DeploymentWithPause:
            if self.config.MaaEmulator_TouchMethod == 'maatouch':
                asst.set_instance_option(AssistantHandler.InstanceOptionType.deployment_with_pause, '1')
            else:
                logger.critical('使用了不支持暂停下干员的触控方案')
                raise RequestHumanTakeover

        return asst

    def maa_update_options(self):
        """
        读取 MAA 更新设置。

        设置项属于 "Maa" 任务（MAA 设置页），不随当前运行的 MAA 任务绑定，
        因此使用 cross_get 直接读取配置文件。
        """
        config = self.config
        version_type = config.cross_get(keys='Maa.MaaUpdate.VersionType', default='Stable')
        if not isinstance(version_type, str) or version_type not in Version.__members__:
            version_type = 'Stable'
        return {
            'auto_resource': config.cross_get(keys='Maa.MaaUpdate.AutoUpdateResource', default=True),
            'auto_package': config.cross_get(keys='Maa.MaaUpdate.UpdatePackage', default=False),
            'version': Version[version_type],
            'client': config.MaaEmulator_PackageName,
        }

    def maa_update(self, force=False):
        """
        更新 MAA 任务资源（tasks.json）与 MAA 本体。

        必须在 AssistantHandler.load 之前调用，否则 MaaCore.dll 被占用无法覆盖。
        任何失败都只记录日志，不阻塞后续 MAA 任务。

        Args:
            force (bool): 忽略配置开关，强制检查更新（"更新 MAA" 工具调用）
        """
        path = self.config.MaaEmulator_MaaPath
        if not path or not os.path.isdir(path):
            return False

        options = self.maa_update_options()
        updater = MaaUpdater(path, options['version'])
        do_resource = force or options['auto_resource']
        do_package = force or options['auto_package']
        if not (do_resource or do_package or updater.has_pending()):
            return False

        logger.hr('MAA 更新检查', level=2)
        try:
            # 安装上次下载完成但未能安装的更新包（此时 MaaCore 尚未加载）
            updater.apply_pending()
            if do_resource:
                updater.update_resource(force=force, client=options['client'])
            if do_package:
                updater.check_package_update(apply_now=True)
            updater.clean_update_cache()
        except Exception as e:
            logger.exception(e)
            logger.warning('MAA 更新失败，将使用当前版本继续运行')
            return False
        return True

    def maa_asst(self):
        """
        取 MaaCore 实例，等待期间也能用。

        与 `asst` 的区别是不做「先跑开始唤醒」的检查，因此可以在任务之外调用。
        """
        # 与 `asst` 共用同一份缓存（cached_property 把结果存在实例 __dict__ 里）
        asst = self.__dict__.get('asst')
        if asst is not None:
            return asst

        asst = self.load_asst(check_task=False)
        self.__dict__['asst'] = asst
        return asst

    def maa_close_game_during_wait(self):
        """
        任务队列清空（等待下一个任务）时的收尾动作，
        对应 MAA 设置页「优化设置 - 完成所有任务后」。

        Returns:
            bool: True 表示游戏已被关闭，下个任务开始前需要补一次开始唤醒
        """
        method = self.config.Optimization_WhenTaskQueueEmpty
        if method != 'close_game':
            logger.info('所有任务已完成，游戏停留在主页面待机')
            return False

        logger.info('所有任务已完成，正在关闭游戏')
        try:
            handler = AssistantHandler(config=self.config, asst=self.maa_asst())
            handler.connect()
            handler.maa_start('CloseDown', {
                'client_type': self.config.MaaEmulator_PackageName,
            })
        except Exception as e:
            logger.exception(e)
            logger.warning('关闭游戏失败，将保持游戏运行')
            return False
        return True

    def get_next_task(self):
        """
        重写任务调度，支持「完成所有任务后」的收尾行为。

        ALAS 主模块的等待逻辑依赖 device.app_stop() 与 Restart 任务，
        MAA 插件既没有自己的 device 也没有 Restart 任务，
        所以等待期间由这里直接调用 MAA 的 CloseDown 关闭游戏，
        下个任务开始前再补一次「开始唤醒」把游戏拉起来。
        """
        while 1:
            task = self.config.get_next()
            self.config.task = task
            self.config.bind(task)

            if task.next_run > datetime.now():
                logger.info(f'Wait until {task.next_run} for task `{task.command}`')
                self.is_first_task = False
                closed = self.maa_close_game_during_wait()
                if closed:
                    # 先排一次开始唤醒，等待期间配置被改动也不会带着关掉的游戏去跑任务
                    self.config.task_call('MaaStartup')
                self.device.release_during_wait()
                if not self.wait_until(task.next_run):
                    del_cached_property(self, 'config')
                    continue
                if closed:
                    del_cached_property(self, 'config')
                    continue
            break

        AzurLaneConfig.is_hoarding_task = False
        return task.command

    def maa_startup(self):
        AssistantHandler(config=self.config, asst=self.asst).startup()

    def maa_annihilation(self):
        AssistantHandler(config=self.config, asst=self.asst).fight()

    def maa_material(self):
        AssistantHandler(config=self.config, asst=self.asst).fight()

    def maa_fight(self):
        AssistantHandler(config=self.config, asst=self.asst).fight()

    def maa_recruit(self):
        AssistantHandler(config=self.config, asst=self.asst).recruit()

    def maa_infrast(self):
        AssistantHandler(config=self.config, asst=self.asst).infrast()

    def maa_mall(self):
        AssistantHandler(config=self.config, asst=self.asst).mall()

    def maa_award(self):
        AssistantHandler(config=self.config, asst=self.asst).award()

    def maa_roguelike(self):
        AssistantHandler(config=self.config, asst=self.asst).roguelike()

    def maa_reclamation_algorithm(self):
        AssistantHandler(config=self.config, asst=self.asst).reclamation_algorithm()

    def maa_data_update(self):
        AssistantHandler(config=self.config, asst=self.asst).data_update()

    def maa_oper_progress(self):
        AssistantHandler(config=self.config, asst=self.asst).oper_progress()


def loop(config_name):
    ArknightsAutoScript(config_name).loop()


def set_stop_event(e):
    ArknightsAutoScript.stop_event = e


def maa_copilot(config_name):
    script = ArknightsAutoScript(config_name)
    script.config.bind('MaaCopilot')
    handler = AssistantHandler(config=script.config, asst=script.asst)
    handler.connect()
    handler.copilot()


def maa_update(config_name):
    script = ArknightsAutoScript(config_name)
    script.config.bind('MaaUpdate')
    script.maa_update(force=True)
