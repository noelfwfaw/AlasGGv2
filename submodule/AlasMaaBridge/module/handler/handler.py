import os
import re
import json
import time
import requests
import datetime
from typing import Any

from cached_property import cached_property

from deploy.config import DeployConfig
from module.base.timer import Timer
from module.config.deep import deep_get
from module.config.utils import read_file, get_server_last_update
from module.device.connection_attr import ConnectionAttr
from module.exception import RequestHumanTakeover
from module.logger import logger

from submodule.AlasMaaBridge.module.config.config import ArknightsConfig
from submodule.AlasMaaBridge.module.asst import asst, utils


# 肉鸽主题
ROGUELIKE_THEMES = ['Phantom', 'Mizuki', 'Sami', 'Sarkaz', 'JieGarden', 'BlackFlow']
# 所有主题通用的分队
ROGUELIKE_COMMON_SQUADS = [
    '指挥分队', '后勤分队',
    '突击战术分队', '堡垒战术分队', '远程战术分队', '破坏战术分队',
]
# 主题专属分队，参照 MAA GUI 的分队表（resource/roguelike/<theme>/squad.json）
ROGUELIKE_THEME_SQUADS = {
    'Phantom': ['集群分队', '矛头分队', '研究分队'],
    'Mizuki': ['集群分队', '矛头分队', '心胜于物分队', '物尽其用分队', '以人为本分队', '研究分队'],
    'Sami': ['集群分队', '矛头分队', '永恒狩猎分队', '生活至上分队', '科学主义分队', '特训分队'],
    'Sarkaz': [
        '集群分队', '矛头分队',
        '魂灵护送分队', '博闻广记分队', '蓝图测绘分队', '因地制宜分队',
        '异想天开分队', '点刺成锭分队', '拟态学者分队', '专业人士分队',
    ],
    'JieGarden': [
        '特勤分队', '高台突破分队', '地面突破分队', '游客分队', '司岁台分队',
        '天师府分队', '花团锦簇分队', '棋行险着分队', '岁影回音分队',
        '代理人分队', '知学分队', '商贾分队',
    ],
    'BlackFlow': [
        '特勤分队', '矛头分队', '高台突破分队', '地面突破分队',
        '本源研修分队', '文明开化分队', '开拓者分队', '多边贸易分队',
        '地质调查分队', '团结阵线分队', '踏足遍历分队',
    ],
}
# 高规格分队只有这几个主题可选
ROGUELIKE_ELITE_SQUAD_THEMES = ('Phantom', 'Mizuki', 'Sami', 'Sarkaz', 'JieGarden')
# 只有界园和黑流树海有这两个职业组
ROGUELIKE_NEW_ROLES = ('灵活部署', '坚不可摧')
ROGUELIKE_NEW_ROLE_THEMES = ('JieGarden', 'BlackFlow')
# 开局直升只对这几个分队有意义
ROGUELIKE_ELITE_TWO_SQUADS = ('突击战术分队', '堡垒战术分队', '远程战术分队', '破坏战术分队')
# MaaCore v6 认可的肉鸽模式（3 = 旧版结局模式，v6 已移除，传了会整任务被拒）
ROGUELIKE_MODES = (0, 1, 4, 5, 6, 7, 10001, 20001, 30001, 30003)
# 黑流树海只接受这几个模式，4/6/7 会被 MaaCore 直接拒绝
ROGUELIKE_BLACKFLOW_MODES = (0, 1, 30001, 30003)
# 主题专属模式，用错主题 MAA 会找不到层数策略而全程避战
ROGUELIKE_MODE_THEMES = {
    5: ('Sami',),
    10001: ('Sarkaz',),
    20001: ('JieGarden',),
    30001: ('BlackFlow',),
    30003: ('BlackFlow',),
}
# 刷开局时可凹的奖励，中文/英文关键字都认
ROGUELIKE_COLLECTIBLE_REWARDS = {
    '热水壶': 'hot_water', '护盾': 'shield', '源石锭': 'ingot', '希望': 'hope',
    '随机': 'random', '钥匙': 'key', '骰子': 'dice', '构想': 'ideas', '票券': 'ticket',
}
ROGUELIKE_COLLECTIBLE_REWARDS_EN = (
    'hot_water', 'shield', 'ingot', 'hope', 'random', 'key', 'dice', 'ideas', 'ticket'
)
# 只有特定主题才有的开局奖励，填错会一直凹不到
ROGUELIKE_REWARD_THEMES = {
    'key': ('Mizuki',),
    'dice': ('Mizuki',),
    'ideas': ('Sarkaz',),
    'ticket': ('JieGarden',),
    'hope': ('Phantom', 'Mizuki', 'Sami', 'Sarkaz', 'BlackFlow'),
}


# 黑流树海刷开局：襁褓动物，最多两只且至多一只羽蛇
BLACKFLOW_ANIMALS = {
    '猫': 'swaddled_cat', '狗': 'swaddled_dog',
    '三头犬': 'swaddled_cerberus', '羽蛇': 'swaddled_feathered_serpent',
    'swaddled_cat': 'swaddled_cat', 'swaddled_dog': 'swaddled_dog',
    'swaddled_cerberus': 'swaddled_cerberus',
    'swaddled_feathered_serpent': 'swaddled_feathered_serpent',
}
# 黑流树海刷开局：开局奖励按位传递，位序与 MaaCore 的 RewardCards 一致
BLACKFLOW_REWARDS = {
    '未编号物': 1 << 0, '调查预付款': 1 << 1, '空间租赁': 1 << 2,
    '退行补偿': 1 << 3, '林间代步': 1 << 4, '巢寄生': 1 << 5,
    '天马': 1 << 6, '骏鹰': 1 << 7, '巨龙': 1 << 8,
    '九头蛇': 1 << 9, '白泽': 1 << 10, '金乌': 1 << 11,
}
# 生息演算主题与各自合法的模式（v6 的 mode 编码不通用）
RECLAMATION_THEME_MODES = {
    'Tales': (0, 1),
    'RelaunchAnchor': (16, 32, 48),
}
RECLAMATION_DEFAULT_MODE = {'Tales': 0, 'RelaunchAnchor': 16}


def roguelike_squads(theme):
    """某主题下 MAA 认得的全部分队。"""
    squads = list(ROGUELIKE_COMMON_SQUADS)
    squads += ROGUELIKE_THEME_SQUADS.get(theme, [])
    if theme in ROGUELIKE_ELITE_SQUAD_THEMES:
        squads.append('高规格分队')
    return squads


def split_keywords(string):
    """把 `A > B > C` 或换行、逗号分隔的文本切成列表。"""
    if not string:
        return []
    string = string.replace('\n', '>').replace('\r', '>').replace(',', '>').replace('，', '>')
    return [word.strip() for word in string.split('>') if word.strip()]


def build_roguelike_params(config):
    """
    把 MaaRoguelike 配置翻译成 MAA Roguelike 任务的参数。

    这里只做参数拼装，不依赖 MAA 实例，方便单独验证。
    每个参数都按 MAA v6 的 integration.md 及 MaaCore 源码取值。
    """
    theme = config.MaaRoguelike_Theme
    if theme not in ROGUELIKE_THEMES:
        logger.warning(f'未知的肉鸽主题 {theme}，已改用 Sarkaz')
        theme = 'Sarkaz'
    mode = int(config.MaaRoguelike_Mode)
    difficulty = int(config.MaaRoguelike_Difficulty)

    # 旧配置可能残留 v6 已移除的模式（如 3 = 结局），MaaCore 会拒绝整个任务
    if mode not in ROGUELIKE_MODES:
        logger.warning(f'肉鸽模式 {mode} 不被 MAA 支持，已改用模式 0')
        mode = 0

    # 主题专属模式用在别的主题上会让 MAA 找不到层数策略，直接退回刷经验
    limit = ROGUELIKE_MODE_THEMES.get(mode)
    if limit and theme not in limit:
        logger.warning(f'模式 {mode} 仅适用于 {"、".join(limit)} 主题，已改用模式 0')
        mode = 0

    # 黑流树海只接受 0/1/30001/30003，其余模式 MaaCore 会直接拒绝整个任务
    if theme == 'BlackFlow' and mode not in ROGUELIKE_BLACKFLOW_MODES:
        logger.warning(f'{theme} 主题不支持模式 {mode}，已改用模式 0')
        mode = 0

    squad = config.MaaRoguelike_Squad
    if squad not in roguelike_squads(theme):
        logger.warning(f'分队 {squad} 不适用于 {theme} 主题，已改用指挥分队')
        squad = '指挥分队'

    roles = config.MaaRoguelike_Roles
    if roles in ROGUELIKE_NEW_ROLES and theme not in ROGUELIKE_NEW_ROLE_THEMES:
        logger.warning(f'职业组 {roles} 不适用于 {theme} 主题，已改用稳扎稳打')
        roles = '稳扎稳打'

    args = {
        'theme': theme,
        'mode': mode,
        'difficulty': difficulty,
        'starts_count': int(config.MaaRoguelike_StartsCount),
        'squad': squad,
        'roles': roles,
        'investment_enabled': bool(config.MaaRoguelike_InvestmentEnabled),
        'stop_when_investment_full': bool(config.MaaRoguelike_StopWhenInvestmentFull),
        'stop_at_final_boss': bool(config.MaaRoguelike_StopAtFinalBoss),
        'stop_at_max_level': bool(config.MaaRoguelike_StopAtMaxLevel),
    }
    if args['investment_enabled']:
        args['investments_count'] = int(config.MaaRoguelike_InvestmentsCount)
        args['investment_with_more_score'] = bool(config.MaaRoguelike_InvestmentWithMoreScore)

    if config.MaaRoguelike_CoreChar:
        args['core_char'] = config.MaaRoguelike_CoreChar
    if config.MaaRoguelike_Support != 'no_use':
        args['use_support'] = True
    if config.MaaRoguelike_Support == 'nonfriend_support':
        args['use_nonfriend_support'] = True

    # 刷开局（模式 4）里的直升
    elite_two = config.MaaRoguelike_startWithEliteTwo
    if mode == 4 and squad in ROGUELIKE_ELITE_TWO_SQUADS and elite_two != 'no_use':
        args['start_with_elite_two'] = True
        args['only_start_with_elite_two'] = elite_two == 'only_start_with_elite_two'

    if theme == 'Mizuki':
        args['refresh_trader_with_dice'] = bool(config.MaaRoguelike_refreshTraderWithDice)

    if mode == 4:
        args['collectible_mode_shopping'] = bool(config.MaaRoguelike_CollectibleModeShopping)
        collectible_squad = config.MaaRoguelike_CollectibleModeSquad or squad
        if collectible_squad not in roguelike_squads(theme):
            logger.warning(f'刷开局分队 {collectible_squad} 不适用于 {theme} 主题，已改用 {squad}')
            collectible_squad = squad
        args['collectible_mode_squad'] = collectible_squad
        rewards = {key: False for key in ROGUELIKE_COLLECTIBLE_REWARDS_EN}
        for word in split_keywords(config.MaaRoguelike_CollectibleModeRewards):
            key = ROGUELIKE_COLLECTIBLE_REWARDS.get(word, word)
            if key not in rewards:
                logger.warning(f'未知的开局奖励 {word}，已忽略')
                continue
            limit = ROGUELIKE_REWARD_THEMES.get(key)
            if limit and theme not in limit:
                logger.warning(f'开局奖励 {word} 只适用于 {"、".join(limit)} 主题，已忽略')
                continue
            rewards[key] = True
        args['collectible_mode_start_list'] = rewards

    if mode == 6:
        args['monthly_squad_auto_iterate'] = bool(config.MaaRoguelike_MonthlySquadAutoIterate)
        args['monthly_squad_check_comms'] = bool(config.MaaRoguelike_MonthlySquadCheckComms)
    if mode == 7:
        args['deep_exploration_auto_iterate'] = bool(config.MaaRoguelike_DeepExplorationAutoIterate)
    if mode == 20001:
        # 必须 1~3，MaaCore 会整任务拒绝越界值
        target = max(1, min(3, int(config.MaaRoguelike_FindplaytimeTarget)))
        args['find_playTime_target'] = target

    if theme == 'Sami':
        if config.MaaRoguelike_SamiFirstFloorFoldartal:
            args['first_floor_foldartal'] = config.MaaRoguelike_SamiFirstFloorFoldartal
        foldartals = split_keywords(config.MaaRoguelike_SamiStartFoldartals)
        if foldartals:
            args['start_foldartal_list'] = foldartals
        if mode == 5:
            paradigms = split_keywords(config.MaaRoguelike_ExpectedCollapsalParadigms)
            if paradigms:
                args['expected_collapsal_paradigms'] = paradigms

    if config.MaaRoguelike_StartWithSeed:
        args['start_with_seed'] = str(config.MaaRoguelike_StartWithSeed)

    # 黑流树海刷经验时要指定打哪个结局，否则 MAA 默认打结局一
    if theme == 'BlackFlow' and mode == 0:
        args['blackflow_strategy'] = config.MaaRoguelike_BlackFlowStrategy

    # 黑流树海刷开局必须带上襁褓动物与开局奖励，否则 MaaCore 直接拒绝整个任务
    if mode == 30003:
        animals = []
        for word in split_keywords(config.MaaRoguelike_BlackFlowStartAnimals):
            animal = BLACKFLOW_ANIMALS.get(word)
            if not animal:
                logger.warning(f'未知的襁褓动物 {word}，已忽略')
                continue
            if animal not in animals:
                animals.append(animal)
        if len(animals) > 2:
            logger.warning('襁褓动物最多只能填两只，已只保留前两只')
            animals = animals[:2]
        if animals.count('swaddled_feathered_serpent') > 1:
            logger.warning('羽蛇最多只能填一只，已忽略多余的')
            animals = [a for a in animals if a != 'swaddled_feathered_serpent'] \
                + ['swaddled_feathered_serpent']
            animals = animals[:2]
        rewards = 0
        for word in split_keywords(config.MaaRoguelike_BlackFlowStartRewards):
            value = BLACKFLOW_REWARDS.get(word)
            if value is None:
                logger.warning(f'未知的开局奖励 {word}，已忽略')
                continue
            rewards |= value
        # 没有羽蛇时羽蛇专属奖励（位 6 起）不会出现，MaaCore 也会自行屏蔽
        if 'swaddled_feathered_serpent' not in animals:
            rewards &= 0x3F
        args['blackflow_start_animals'] = animals
        args['blackflow_start_rewards'] = rewards

    return args


def build_reclamation_params(config):
    """
    生息演算参数。

    v6 的任务名是 Reclamation（v5 的 ReclamationAlgorithm 已废弃），
    主题只剩「沙洲遗闻」和「重启锚点」，且两者的 mode 编码不通用。
    """
    theme = config.MaaReclamation_Theme
    if theme not in RECLAMATION_THEME_MODES:
        logger.warning(f'未知的生息演算主题 {theme}，已改用沙洲遗闻')
        theme = 'Tales'

    mode = int(config.MaaReclamation_Mode)
    if mode not in RECLAMATION_THEME_MODES[theme]:
        fallback = RECLAMATION_DEFAULT_MODE[theme]
        logger.warning(f'模式 {mode} 不适用于 {theme} 主题，已改用模式 {fallback}')
        mode = fallback

    args = {
        'theme': theme,
        'mode': mode,
        'increment_mode': int(config.MaaReclamation_IncrementMode),
        'num_craft_batches': int(config.MaaReclamation_NumCraftBatches),
    }
    # 有存档刷繁荣点数时 MaaCore 会强制读取该字段，缺失会抛异常，因此总是传入（可为空数组）
    args['tools_to_craft'] = split_keywords(config.MaaReclamation_ToolsToCraft)
    # 只有「沙洲遗闻 - 无存档刷繁荣点数」会用到商店
    if theme == 'Tales' and mode == 0:
        args['clear_store'] = bool(config.MaaReclamation_ClearStore)
    return args


def parse_oper_progress_plans(text):
    """
    解析干员培养计划。

    与 MAA 的「从剪贴板导入计划」格式一致，是一个 JSON 数组，每项指定一个养成动作：
    [
      {"name": "艾雅法拉", "elite": 2},
      {"name": "艾雅法拉", "skill_level": 7},
      {"name": "史尔特尔", "skill_mastery": [3, 0, 0]}
    ]

    Args:
        text (str): 配置里填写的文本

    Returns:
        list[dict]: 可直接作为 OperProgress 的 plans 参数

    Raises:
        ValueError: 格式错误（信息用于提示用户，不应导致任务崩溃）
    """
    if not text or not str(text).strip():
        return []

    try:
        data = json.loads(str(text).strip())
    except ValueError as e:
        raise ValueError(f'不是合法的 JSON（{e}）')
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError('最外层必须是数组 [ ... ]')

    plans = []
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ValueError(f'第 {index} 项不是对象')
        name = str(item.get('name') or '').strip()
        if not name:
            raise ValueError(f'第 {index} 项缺少 name')

        plan = {'name': name}
        if item.get('role'):
            plan['role'] = str(item['role'])

        elite = item.get('elite')
        if elite:
            elite = int(elite)
            if elite not in (1, 2):
                raise ValueError(f'{name} 的 elite 只能是 1 或 2')
            plan['elite'] = elite

        skill_level = item.get('skill_level')
        if skill_level:
            skill_level = int(skill_level)
            if not 2 <= skill_level <= 7:
                raise ValueError(f'{name} 的 skill_level 只能是 2~7')
            plan['skill_level'] = skill_level

        mastery = item.get('skill_mastery')
        if mastery:
            if not isinstance(mastery, list) or len(mastery) != 3:
                raise ValueError(f'{name} 的 skill_mastery 必须是长度为 3 的数组，如 [3, 0, 0]')
            mastery = [int(i) for i in mastery]
            if any(i < 0 or i > 3 for i in mastery):
                raise ValueError(f'{name} 的 skill_mastery 每项只能是 0~3')
            plan['skill_mastery'] = mastery

        if len(plan) == 1:
            raise ValueError(f'{name} 没有指定 elite / skill_level / skill_mastery 中的任意一项')
        plans.append(plan)
    return plans


class AssistantHandler:
    config: ArknightsConfig
    Asst: Any
    Message: Any
    InstanceOptionType: Any
    ASST_HANDLER: Any

    @staticmethod
    def load(path, incremental_path=None):
        AssistantHandler.Asst = asst.Asst
        AssistantHandler.Message = utils.Message
        AssistantHandler.InstanceOptionType = utils.InstanceOptionType
        try:
            loaded = AssistantHandler.Asst.load(path, user_dir=path, incremental_path=incremental_path)
        except OSError as e:
            logger.critical(e)
            logger.critical("MAA加载失败，请检查MAA本体能否正常打开")
            raise RequestHumanTakeover
        if not loaded:
            logger.critical(
                f'无法加载 MAA 资源（{path}），通常是安装不完整或缓存中的增量资源已损坏。'
                f'可删除 {os.path.join(path, "cache")} 后重试，或重新安装 MAA')
            raise RequestHumanTakeover

        AssistantHandler.ASST_HANDLER = None

    def __init__(self, config, asst, task=None):
        """
        Args:
            config (ArknightsConfig, str): Name of the user config under ./config
            asst (Asst):
            task (str): Bind a task only for dev purpose. Usually to be None for auto task scheduling.
        """
        if isinstance(config, str):
            self.config = ArknightsConfig(config, task=task)
        else:
            self.config = config
        self.interval_timer = {}
        AssistantHandler.ASST_HANDLER = self
        self.asst = asst
        self.callback_timer = Timer(600)
        self.serial = None
        self.signal = None
        self.params = None
        self.task_id = None
        self.callback_list = []

    @staticmethod
    def split_filter(string, sep='>'):
        return [f.strip(' \t\r\n') for f in string.split(sep)]

    def maa_stop(self):
        self.callback_list.append(self.task_end_callback)
        self.asst.stop()
        while 1:
            if self.callback_timer.reached():
                logger.critical('MAA no respond, probably stuck')
                raise RequestHumanTakeover

            if self.signal in [
                self.Message.AllTasksCompleted,
                self.Message.TaskChainCompleted,
                self.Message.TaskChainStopped,
                self.Message.TaskChainError
            ]:
                return

    def maa_start(self, task_name, params):
        logger.hr('MAA start')
        logger.info(f'Task name: {task_name}, params={params}')
        self.task_id = self.asst.append_task(task_name, params)
        # append_task 返回 0 表示 MaaCore 拒绝了该任务（参数非法、类型不存在等）。
        # 此时队列为空，asst.start() 不会有任何回调，只会空等到 600 秒超时。
        if not self.task_id:
            logger.critical(f'MAA 拒绝添加任务 {task_name}，参数非法或任务类型不存在，请检查对应的 MAA 设置')
            raise RequestHumanTakeover
        self.signal = None
        self.params = params
        self.callback_list.append(self.task_end_callback)
        self.callback_timer.reset()
        self.asst.start()
        while 1:
            if self.callback_timer.reached():
                logger.critical('MAA no respond, probably stuck')
                raise RequestHumanTakeover

            if self.signal is not None:
                if self.signal == self.Message.TaskChainError:
                    raise RequestHumanTakeover
                self.maa_stop()
                self.callback_list.clear()
                return

            time.sleep(0.5)

    def task_end_callback(self, m, d):
        """
        从MAA的回调中处理任务结束的信息。

        所有其他回调处理函数应遵循同样格式，
        在需要使用的时候加入callback_list，
        可以被随时移除，或在任务结束时自动清空。
        参数的详细说明见https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/master/docs/zh-cn/protocol/callback-schema.md

        Args:
            m (Message): 消息类型
            d (dict): 消息详情
        """
        self.callback_timer.reset()
        if m in [
            self.Message.AllTasksCompleted,
            self.Message.TaskChainError,
            self.Message.TaskChainStopped
        ]:
            self.signal = m
            self.callback_list.remove(self.task_end_callback)

    def penguin_id_callback(self, m, d):
        if not self.config.MaaRecord_PenguinID \
                and m == self.Message.SubTaskExtraInfo \
                and deep_get(d, keys='what') == 'PenguinId':
            self.config.MaaRecord_PenguinID = deep_get(d, keys='details.id')
            self.callback_list.remove(self.penguin_id_callback)

    def annihilation_callback(self, m, d):
        # Skip annihilation error task callback temporary
        # https://github.com/MaaAssistantArknights/MaaAssistantArknights/issues/10623
        ignoreErrorKeywords = ["FightSeries-Indicator", "FightSeries-Icon"]
        if m == self.Message.SubTaskError \
                and deep_get(d, keys='first') not in ignoreErrorKeywords:
            self.signal = m

    def fight_stop_count_callback(self, m, d):
        if m == self.Message.SubTaskCompleted:
            if deep_get(d, keys='details.task') == 'MedicineConfirm' \
                    and self.config.MaaFight_Medicine is not None:
                self.config.MaaFight_Medicine = self.config.MaaFight_Medicine - 1
            elif deep_get(d, keys='details.task') == 'StoneConfirm' \
                    and self.config.MaaFight_Stone is not None:
                self.config.MaaFight_Stone = self.config.MaaFight_Stone - 1

        elif m == self.Message.SubTaskExtraInfo \
                and deep_get(d, keys='what') == 'StageDrops':
            if self.config.MaaFight_Times is not None:
                self.config.MaaFight_Times = self.config.MaaFight_Times - 1

            if self.config.MaaFight_Drops is not None:
                drop_list = deep_get(d, keys='details.drops')
                if drop_list is not None:
                    def replace(matched):
                        value = int(matched.group('value')) - drop['quantity']
                        if value <= 0:
                            raise ValueError
                        return re.sub(r':\d+', f':{value}', matched.group())

                    drops_filter = self.config.MaaFight_Drops
                    try:
                        for drop in drop_list:
                            drops_filter = re.sub(f'{drop["itemId"]}:(?P<value>\\d+)', replace, drops_filter)
                            drops_filter = re.sub(f'{drop["itemName"]}:(?P<value>\\d+)', replace, drops_filter)
                    except ValueError:
                        drops_filter = None
                    self.config.MaaFight_Drops = drops_filter

    def roguelike_callback(self, m, d):
        if self.task_switch_timer.reached():
            if self.config.task_switched():
                self.task_switch_timer = None
                self.params['starts_count'] = 0
                self.asst.set_task_params(self.task_id, self.params)
                self.callback_list.remove(self.roguelike_callback)
            else:
                self.task_switch_timer.reset()

    def serial_check(self):
        """
        serial check
        """
        if self.is_bluestacks4_hyperv:
            self.serial = ConnectionAttr.find_bluestacks4_hyperv(self.serial)
        if self.is_bluestacks5_hyperv:
            self.serial = ConnectionAttr.find_bluestacks5_hyperv(self.serial)

    @cached_property
    def is_bluestacks4_hyperv(self):
        return "bluestacks4-hyperv" in self.serial

    @cached_property
    def is_bluestacks5_hyperv(self):
        return "bluestacks5-hyperv" in self.serial

    def connect(self):
        adb = os.path.abspath(DeployConfig().AdbExecutable)
        self.serial = self.config.MaaEmulator_Serial
        self.serial_check()

        old_callback_list = self.callback_list
        self.callback_list = []

        if not self.asst.connect(adb, self.serial):
            raise RequestHumanTakeover

        self.callback_list = old_callback_list

    def startup(self):
        self.connect()
        if self.config.Scheduler_NextRun.strftime('%H:%M') == self.config.Scheduler_ServerUpdate:
            self.maa_start('CloseDown', {
                "client_type": self.config.MaaEmulator_PackageName
            })

        self.maa_start('StartUp', {
            "client_type": self.config.MaaEmulator_PackageName,
            "start_game_enabled": True
        })
        self.config.task_delay(server_update=True)

    def fight(self):
        args = {
            "report_to_penguin": self.config.MaaRecord_ReportToPenguin,
            "report_to_yituliu": self.config.MaaRecord_ReportToYiTuLiu,
            "client_type": self.config.MaaEmulator_PackageName,
            "DrGrandet": self.config.MaaFight_DrGrandet,
            "series": int(self.config.MaaFight_Series)
        }
        # Set stage
        if self.config.MaaFight_Stage == 'last':
            args['stage'] = ''
        elif self.config.MaaFight_Stage == 'custom':
            args['stage'] = self.config.MaaFight_CustomStage
        else:
            args['stage'] = self.config.MaaFight_Stage

        # Set weekly stage
        if self.config.MaaFightWeekly_Enable:
            today = get_server_last_update('04:00').strftime('%A')
            logger.attr('Weekday', today)
            stage = self.config.__getattribute__(f'MaaFightWeekly_{today}')
            if stage != 'default':
                logger.info(f'Using stage setting from {today}: {stage}')
                args['stage'] = stage

        if self.config.MaaFight_Medicine is not None:
            args["medicine"] = self.config.MaaFight_Medicine
        if self.config.MaaFight_MedicineTactics == 'run_out':
            args["medicine"] = 999
        if self.config.MaaFight_MedicineTactics == 'expiring':
            args["expiring_medicine"] = 999
        if self.config.MaaFight_Stone is not None:
            args["stone"] = self.config.MaaFight_Stone
        if self.config.MaaFight_Times is not None:
            args["times"] = self.config.MaaFight_Times

        if self.config.MaaFight_Drops:
            old = read_file(os.path.join(self.config.MaaEmulator_MaaPath, './resource/item_index.json'))
            new = {}
            for key, value in old.items():
                new[value['name']] = key
            drops = {}
            drops_filter = self.split_filter(self.config.MaaFight_Drops)
            for drop in drops_filter:
                drop = self.split_filter(drop, sep=':')
                try:
                    drops[new[drop[0]]] = int(drop[1])
                except KeyError:
                    drops[drop[0]] = int(drop[1])
            args['drops'] = drops

        if self.config.MaaRecord_ReportToPenguin and self.config.MaaRecord_PenguinID:
            args["penguin_id"] = self.config.MaaRecord_PenguinID
        elif self.config.MaaRecord_ReportToPenguin and not self.config.MaaRecord_PenguinID:
            self.callback_list.append(self.penguin_id_callback)

        if self.config.task.command == 'MaaMaterial':
            self.callback_list.append(self.fight_stop_count_callback)
        if self.config.task.command == 'MaaAnnihilation':
            self.callback_list.append(self.annihilation_callback)

        self.maa_start('Fight', args)

        if self.config.task.command == 'MaaAnnihilation':
            self.config.task_delay(server_update=True)
        elif self.config.task.command == 'MaaMaterial':
            if self.signal == self.Message.AllTasksCompleted:
                with self.config.multi_set():
                    self.config.MaaFight_Medicine = None
                    self.config.MaaFight_Stone = None
                    self.config.MaaFight_Times = None
                    self.config.MaaFight_Drops = None
                    self.config.Scheduler_Enable = False
            else:
                self.config.task_delay(success=False)
        else:
            self.config.task_call('MaaAward', force_call=False)
            self.config.task_delay(success=True)

    def recruit(self):
        confirm = []
        if self.config.MaaRecruit_Select3:
            confirm.append(3)
        if self.config.MaaRecruit_Select4:
            confirm.append(4)
        if self.config.MaaRecruit_Select5:
            confirm.append(5)

        args = {
            "refresh": self.config.MaaRecruit_Refresh,
            "select": [4, 5, 6],
            "confirm": confirm,
            "times": self.config.MaaRecruit_Times,
            "expedite": self.config.MaaRecruit_Expedite,
            "skip_robot": self.config.MaaRecruit_SkipRobot,
            "extra_tags_mode": self.config.MaaRecruit_ExtraTagsMode
        }

        if self.config.MaaRecruit_Level3ShortTime:
            args['recruitment_time'] = {'3': 460}

        if self.config.MaaRecord_ReportToPenguin and self.config.MaaRecord_PenguinID:
            args["penguin_id"] = self.config.MaaRecord_PenguinID
        elif self.config.MaaRecord_ReportToPenguin and not self.config.MaaRecord_PenguinID:
            self.callback_list.append(self.penguin_id_callback)

        if self.config.MaaRecruit_FirstTags:
            args["first_tags"] = self.config.MaaRecruit_FirstTags.split(';')

        self.maa_start('Recruit', args)
        self.config.task_delay(success=True)

    def infrast(self):
        args = {
            "facility": self.split_filter(self.config.MaaInfrast_Facility),
            "drones": self.config.MaaInfrast_Drones,
            "threshold": self.config.MaaInfrast_WorkThreshold / 24,
            "replenish": self.config.MaaInfrast_Replenish,
            "dorm_notstationed_enabled": self.config.MaaInfrast_Notstationed,
            "dorm_trust_enabled": self.config.MaaInfrast_Trust,
            "continue_training": self.config.MaaInfrast_ContinueTraining
        }

        if self.config.MaaCustomInfrast_Enable:
            infrast_dict = {
                '153-3': r'resource\custom_infrast\153_layout_3_times_a_day.json',
                '243-3': r'resource\custom_infrast\243_layout_3_times_a_day.json',
                '243-4': r'resource\custom_infrast\243_layout_4_times_a_day.json',
                '252-3': r'resource\custom_infrast\252_layout_3_times_a_day.json',
                '333-3': r'resource\custom_infrast\333_layout_for_Orundum_3_times_a_day.json'
            }
            if self.config.MaaCustomInfrast_BuiltinConfig != 'custom':
                self.config.MaaCustomInfrast_Filename = os.path.join(
                    self.config.MaaEmulator_MaaPath,
                    infrast_dict[self.config.MaaCustomInfrast_BuiltinConfig]
                )
            args['mode'] = 10000
            args['filename'] = self.config.MaaCustomInfrast_Filename

            end_time = datetime.datetime.now() + datetime.timedelta(minutes=30)
            plans = deep_get(read_file(self.config.MaaCustomInfrast_Filename), keys='plans')
            periods = deep_get(plans[0], keys='period')

            if periods is None:
                if self.config.MaaCustomInfrast_CustomPeriod == 'null':
                    logger.critical('无法找到配置文件中的排班周期，请检查文件是否有效')
                    raise RequestHumanTakeover
                else:
                    args['plan_index'] = self.config.MaaCustomInfrast_PlanIndex
            else:
                for i in range(len(plans)):
                    periods = deep_get(plans[i], keys='period')
                    for j, period in enumerate(periods):
                        start_time = datetime.datetime.combine(
                            datetime.date.today(),
                            datetime.datetime.strptime(period[0], '%H:%M').time()
                        )
                        end_time = datetime.datetime.combine(
                            datetime.date.today(),
                            datetime.datetime.strptime(period[1], '%H:%M').time()
                        )
                        now_time = datetime.datetime.now()
                        if start_time <= now_time <= end_time:
                            args['plan_index'] = i
                            # 处理跨天的情形
                            # 如："period": [["22:00", "23:59"], ["00:00","06:00"]]
                            if j != len(periods) - 1 and period[1] == '23:59' and periods[j + 1][0] == '00:00':
                                end_time = datetime.datetime.combine(
                                    datetime.date.today() + datetime.timedelta(days=1),
                                    datetime.datetime.strptime(periods[j + 1][1], '%H:%M').time()
                                )
                            break
                    if 'plan_index' in args:
                        break

            self.maa_start('Infrast', args)
            if periods is None:
                custom_period = self.config.MaaCustomInfrast_CustomPeriod.replace('，', ',').split(',')
                custom_period = [int(x) for x in custom_period]
                self.config.task_delay(minute=60 * custom_period[self.config.MaaCustomInfrast_PlanIndex])
                self.config.MaaCustomInfrast_PlanIndex = (self.config.MaaCustomInfrast_PlanIndex + 1) % len(plans)
            else:
                self.config.task_delay(target=end_time + datetime.timedelta(minutes=1))
        else:
            if self.config.MaaInfrast_WorkThreshold <= self.config.MaaInfrast_ShiftThreshold:
                logger.warning('基建换班心情阈值必须小于基建工作心情阈值，请调整基建设置')
                raise RequestHumanTakeover

            self.maa_start('Infrast', args)
            # 根据心情阈值计算下次换班时间
            # (基建工作心情阈值 - 基建换班心情阈值) / 0.75 * 60
            t = (self.config.MaaInfrast_WorkThreshold - self.config.MaaInfrast_ShiftThreshold) * 80
            self.config.task_delay(minute=t)

    def mall(self):
        buy_first = self.split_filter(self.config.MaaMall_BuyFirst)
        blacklist = self.split_filter(self.config.MaaMall_BlackList)
        credit_fight = self.config.MaaMall_CreditFight
        if self.config.cross_get(keys='MaaMaterial.MaaFight.Stage') == 'last' \
                and self.config.is_task_enabled('MaaMaterial'):
            credit_fight = False
        if self.config.cross_get(keys='MaaFight.MaaFight.Stage') == 'last' \
                and self.config.is_task_enabled('MaaFight'):
            credit_fight = False
        self.maa_start('Mall', {
            "credit_fight": credit_fight,
            "shopping": self.config.MaaMall_Shopping,
            "buy_first": buy_first,
            "blacklist": blacklist,
            "force_shopping_if_credit_full": self.config.MaaMall_ForceShoppingIfCreditFull,
            "only_buy_discount": self.config.MaaMall_OnlyBuyDiscount,
            "reserve_max_credit": self.config.MaaMall_ReserveMaxCredit,
        })
        self.config.task_delay(server_update=True)

    def award(self):
        args = {
            "enable": True,
            "award": self.config.MaaAward_DailyAward,
            "mail": self.config.MaaAward_Mail,
            "recruit": self.config.MaaAward_Recruit,
            "orundum": self.config.MaaAward_Orundum,
            "mining": self.config.MaaAward_Mining,
            "specialaccess": self.config.MaaAward_Specialaccess,
            "signinevent": self.config.MaaAward_SignInEvent,
        }
        self.maa_start('Award', args)
        self.config.task_delay(server_update=True)

    def roguelike(self):
        args = build_roguelike_params(self.config)
        self.task_switch_timer = Timer(30).start()
        self.callback_list.append(self.roguelike_callback)
        self.maa_start('Roguelike', args)

        if self.task_switch_timer is not None:
            self.config.Scheduler_Enable = False

    def reclamation_algorithm(self):
        self.maa_start('Reclamation', build_reclamation_params(self.config))
        self.config.task_delay(server_update=True)

    def data_update(self):
        """
        更新数据：识别干员 box 与仓库。

        MAA 的「更新数据」在 GUI 里就是 OperBox + Depot 两个任务，识别结果由 GUI
        保存/导出。这里把结果写到 config/maa/ 下，供后续查看或导入第三方面板。
        """
        oper_box = self.config.MaaDataUpdate_UpdateOperBox
        depot = self.config.MaaDataUpdate_UpdateDepot
        if not (oper_box or depot):
            logger.warning('「更新数据」没有勾选任何项目，跳过')
            self.config.task_delay(server_update=True)
            return

        if oper_box:
            # maa_start 结束时清空 callback_list，所以每次启动前都要重新挂回调
            self.callback_list.append(self.data_update_callback)
            self.maa_start('OperBox', {})
        if depot:
            self.callback_list.append(self.data_update_callback)
            self.maa_start('Depot', {})
        self.config.task_delay(server_update=True)

    def data_update_callback(self, m, d):
        """
        保存 OperBox / Depot 的识别结果，只在 done=true（识别结束）时写入。
        """
        if m != self.Message.SubTaskExtraInfo:
            return
        what = deep_get(d, keys='what')
        details = deep_get(d, keys='details', default=None) or {}
        if what not in ('OperBoxInfo', 'DepotInfo') or not details.get('done'):
            return

        try:
            if what == 'OperBoxInfo':
                own = details.get('own_opers') or []
                all_opers = details.get('all_opers') or []
                path = self.save_recognition('operbox', own_oper=own, all_oper=all_opers)
                logger.info(f'干员识别完成：拥有 {len(own)} / 共 {len(all_opers)} 名，已保存到 {path}')
            else:
                data = details.get('data')
                # DepotInfo 的 data 是「字符串化的 JSON」，不是对象
                if isinstance(data, str):
                    data = json.loads(data)
                data = data or {}
                path = self.save_recognition('depot', item=data)
                logger.info(f'仓库识别完成：{len(data)} 种材料，已保存到 {path}')
        except Exception as e:
            logger.warning(f'保存 MAA 识别结果失败：{e}')

    @staticmethod
    def save_recognition(name, **data):
        """
        识别结果写入 config/maa/，避免和 config 目录下的配置文件混在一起。
        """
        directory = './config/maa'
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f'{name}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def oper_progress(self):
        """
        干员培养：按填写的计划精英化 / 升技能 / 专精。

        计划格式与 MAA GUI 的「从剪贴板导入」一致，见 parse_oper_progress_plans。
        """
        try:
            plans = parse_oper_progress_plans(self.config.MaaOperProgress_Plans)
        except ValueError as e:
            logger.error(f'干员培养计划格式错误：{e}')
            logger.error('示例：[{"name": "艾雅法拉", "elite": 2}]')
            self.config.task_delay(server_update=True)
            return
        if not plans:
            logger.warning('未填写干员培养计划，跳过本次任务')
            self.config.task_delay(server_update=True)
            return

        logger.info(f'干员培养计划共 {len(plans)} 项：')
        for plan in plans:
            logger.info(f'  {plan}')
        self.callback_list.append(self.oper_progress_callback)
        self.maa_start('OperProgress', {'plans': plans})
        self.config.task_delay(server_update=True)

    def oper_progress_callback(self, m, d):
        if m != self.Message.SubTaskExtraInfo:
            return
        what = deep_get(d, keys='what')
        if what != 'OperProgressDetail':
            return
        name = deep_get(d, keys='details.name')
        result = deep_get(d, keys='details.result')
        detail = deep_get(d, keys='details.result_detail')
        if name:
            logger.info(f'干员培养：{name} -> {result}' + (f'（{detail}）' if detail else ''))

    def copilot(self):
        filename = self.config.MaaCopilot_FileName
        if filename.startswith('maa://'):
            logger.info('正在从神秘代码中下载作业')
            # 不能用 strip，它按字符集裁剪，作业号尾部的 a/m 会被一起吃掉
            homework_id = filename[len('maa://'):]
            r = requests.get(f"https://prts.maa.plus/copilot/get/{homework_id}", timeout=30)
            if r.status_code != 200:
                logger.critical('作业文件下载失败，请检查神秘代码或网络状况')
                raise RequestHumanTakeover
            logger.info('作业下载完毕')

            r.encoding = 'utf-8'
            buf = json.loads(r.text)['data']['content'].encode('utf-8')
            filename = os.path.join(self.config.MaaEmulator_MaaPath, './resource/_temp_copilot.json')
            filename = filename.replace('\\', '/').replace('./', '/').replace('//', '/')
            with open(filename, 'wb') as f:
                f.write(buf)

        homework = read_file(filename)
        stage = deep_get(homework, keys='stage_name')
        if not stage:
            logger.critical('作业文件不存在或已经损坏')
            raise RequestHumanTakeover

        if self.config.MaaCopilot_Identify:
            logger.info(deep_get(homework, keys='doc.title', default='标题：无') + '\n')
            logger.info('\n' + deep_get(homework, keys='doc.details', default='内容：无') + '\n')
            if deep_get(homework, keys='type') == 'SSS':
                out = '\n'
                opers = deep_get(homework, keys='opers')
                if opers:
                    out += '核心干员：\n'
                    for oper in opers:
                        out += f'{oper["name"]}，{oper["skill"]}技能\n'
                    out += '\n'

                tool_men = deep_get(homework, keys='tool_men')
                if tool_men:
                    out += f'工具人：{tool_men}\n\n'

                equipment = deep_get(homework, keys='equipment')
                if equipment:
                    out += f'战术装备（横向）：{equipment}\n\n'

                logger.info(out)
            return

        args = {
            "stage_name": stage,
            "filename": filename,
            "formation": self.config.MaaCopilot_Formation
        }
        for i in range(self.config.MaaCopilot_Cycle):
            if deep_get(homework, keys='type') == 'SSS':
                self.maa_start('SSSCopilot', args)
            else:
                self.maa_start('Copilot', args)
