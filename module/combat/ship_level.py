"""
Ship level records for the WebUI dashboard.

File lives at log/campaign/<config_name>/ship_level.json, shared by
mainline / event battles (fleets) and Operation Siren leveling (os).
"""
import json
import os
from datetime import datetime

from deploy.atomic import atomic_write
from module.logger import logger

OUTPUT_FOLDER = 'log/campaign'
FILE_NAME = 'ship_level.json'


def record_path(config_name):
    """
    Args:
        config_name (str): Alas config name.

    Returns:
        str: Path of the ship level record file.
    """
    return os.path.join(OUTPUT_FOLDER, config_name, FILE_NAME)


def _empty_data():
    """
    Returns:
        dict: Empty record data.
    """
    return {'version': 1, 'fleets': {}, 'os': {}}


def _task_name(config):
    """
    Args:
        config (Config): Alas config.

    Returns:
        str: Command name of the current task, empty if unknown.

        `config.task` is a `Function` object (module/config/config.py:24), not a
        string, so it can not be dumped into json directly. The task name lives
        in its `command` attribute.
    """
    task = getattr(config, 'task', '')
    task = task.command if hasattr(task, 'command') else task
    return task if isinstance(task, str) else ''


def _write(config_name, data):
    """
    Args:
        config_name (str): Alas config name.
        data (dict): Record data to write.
    """
    folder = os.path.join(OUTPUT_FOLDER, config_name)
    os.makedirs(folder, exist_ok=True)
    atomic_write(record_path(config_name), json.dumps(data, ensure_ascii=False, indent=2))


def load_levels(config_name):
    """
    Args:
        config_name (str): Alas config name.

    Returns:
        dict: {
            'version': 1,
            'fleets': {fleet_index (str): {'levels', 'updated', 'task', 'source'}},
            'os': {fleet_index (str): {'levels', 'exp', 'updated', 'task', 'target'}},
        }
        Both 'fleets' and 'os' are always present.
    """
    path = record_path(config_name)
    if not os.path.exists(path):
        return _empty_data()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (ValueError, OSError) as e:
        logger.warning(f'Ship level record load failed: {e}')
        return _empty_data()
    if not isinstance(data, dict):
        return _empty_data()
    data.setdefault('fleets', {})
    data.setdefault('os', {})
    return data


def record_ship_level(config, show_index, levels):
    """
    Record mainline / event fleet levels read from the left side fleet panel.
    Same fleet index is overwritten between tasks, this is intended.

    Args:
        config (Config): Alas config.
        show_index (int): Which fleet row is displayed on screen, 1 or 2.
        levels (list[int]): 6 slots from LevelOcr, 0 means not read.

    Returns:
        bool: True if the record file is updated.
    """
    try:
        if show_index == 1:
            fleet_index, source = config.Fleet_Fleet1, 'Fleet1'
        elif show_index == 2:
            fleet_index, source = config.Fleet_Fleet2, 'Fleet2'
        else:
            return False
        # 0 means the second fleet is not in use, None / str means broken config
        if not isinstance(fleet_index, int) or fleet_index <= 0:
            return False

        data = load_levels(config.config_name)
        read = []
        for i in range(6):
            level = levels[i] if i < len(levels) else 0
            # 0 既可能是空槽位，也可能是这一帧没读到，界面上两者都显示"-"。
            # 不能拿旧值填：队伍换编成后，被撤掉的船会永远挂在卡片上。
            # 一帧读糊了几十秒后的下一场战斗就会自己修回来，不需要兜底。
            read.append(level if isinstance(level, int) and 0 < level <= 200 else 0)
        if not any(read):
            # 整帧 6 格全空，大概率是面板没展开 / 被弹窗挡住，保留旧记录
            return False

        task = _task_name(config)
        data['fleets'][str(fleet_index)] = {
            'levels': read,
            'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'task': task,
            'source': source,
        }
        _write(config.config_name, data)
        return True
    except (OSError, ValueError, TypeError, KeyError) as e:
        # 挂在战斗主流程上，任何异常都不能外抛；但要把异常类型打出来，
        # 否则编程错误（例如 config.task 不是字符串）会变成一句无特征的 warning
        logger.warning(f'Ship level record failed: {type(e).__name__}: {e}')
        return False


def record_os_ship_level(config, fleet_index, ships, target_level=0):
    """
    Record Operation Siren ship levels read from ship detail pages.

    Args:
        config (Config): Alas config.
        fleet_index (int): OpsiFleet_Fleet, 1~4.
        ships (list[tuple]): Each item is (slot, level, exp), slot is
            0-based counted from the flagship.
        target_level (int): OpsiCheckLeveling_TargetLevel, 0 if unknown.

    Returns:
        bool: True if the record file is updated.
    """
    try:
        if not isinstance(fleet_index, int) or not 1 <= fleet_index <= 4:
            return False

        data = load_levels(config.config_name)
        old = data['os'].get(str(fleet_index), {})

        def _pad(values):
            values = values if isinstance(values, list) else []
            return [values[i] if i < len(values) else 0 for i in range(6)]

        old_levels = _pad(old.get('levels'))
        old_exp = _pad(old.get('exp'))
        merged_levels = list(old_levels)
        merged_exp = list(old_exp)
        for item in ships:
            try:
                slot, level, exp = item
            except (TypeError, ValueError):
                continue
            if not isinstance(slot, int) or not 0 <= slot <= 5:
                continue
            # Level range is already validated in ship_info_get_level_exp, double check here
            if isinstance(level, int) and 0 < level <= 125:
                merged_levels[slot] = level
            if isinstance(exp, int) and exp >= 0:
                merged_exp[slot] = exp
        # Reading only a few ships is common in Operation Siren, no full-fleet requirement here
        if not any(merged_levels):
            return False

        data['os'][str(fleet_index)] = {
            'levels': merged_levels,
            'exp': merged_exp,
            'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'task': _task_name(config),
            'target': target_level,
        }
        _write(config.config_name, data)
        return True
    except (OSError, ValueError, TypeError, KeyError) as e:
        logger.warning(f'Ship level record failed: {type(e).__name__}: {e}')
        return False
