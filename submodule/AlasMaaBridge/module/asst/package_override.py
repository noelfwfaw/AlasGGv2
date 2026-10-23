import json
import os
import re

from module.exception import RequestHumanTakeover
from module.logger import logger

# MaaCore 的客户端类型 -> 官方包名，与 <MaaPath>/resource/config.json 的 packageName 一致
DEFAULT_PACKAGE_NAME = {
    'Official': 'com.hypergryph.arknights',
    'Bilibili': 'com.hypergryph.arknights.bilibili',
    'YoStarEN': 'com.YoStarEN.Arknights',
    'YoStarJP': 'com.YoStarJP.Arknights',
    'YoStarKR': 'com.YoStarKR.Arknights',
    'txwy': 'tw.txwy.and.arknights',
}

PACKAGE_NAME_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$')


def resolve_package_name(client_type, custom_package):
    """
    返回最终应使用的包名，不写文件。

    自定义包名格式非法时直接 RequestHumanTakeover：静默降级成官方包名
    会让用户以为配置生效了，实际跑的是另一个客户端。

    Args:
        client_type (str): MAA 的客户端类型，如 Official
        custom_package (str): 用户填写的自定义包名，留空表示使用官方预设

    Returns:
        str: 最终包名
    """
    custom = str(custom_package or '').strip()
    if custom:
        if '/' in custom:
            logger.critical(
                f'自定义包名 {custom} 中出现了 "/"，这里只填包名，不要填 Activity（形如 `包名/Activity`）'
            )
            raise RequestHumanTakeover
        if not PACKAGE_NAME_PATTERN.match(custom):
            logger.critical(
                f'自定义包名 {custom} 格式非法，应形如 com.hypergryph.arknights'
            )
            raise RequestHumanTakeover
        return custom

    default = DEFAULT_PACKAGE_NAME.get(client_type)
    if not default:
        logger.critical(f'未知的客户端类型 {client_type}，无法确定对应的官方包名')
        raise RequestHumanTakeover
    return default


def apply_package_override(maa_path, client_type, custom_package):
    """
    把最终包名写入 <maa_path>/resource/config.json 的 packageName[client_type]。

    MaaCore 在 AsstLoadResource() 时一次性读入资源，之后改文件无效，
    因此必须在加载资源之前调用。

    留空（使用预设）时不覆盖文件里已有的非预设值，只告警并沿用文件中的值。
    这类值通常来自用户为解决魔改客户端问题的手动修改，覆盖回去会把能跑的配置改坏；
    但也必须明确告警，否则用户会误以为跑的是官方包名。

    Args:
        maa_path (str): MAA 安装路径
        client_type (str): MAA 的客户端类型，如 Official
        custom_package (str): 用户填写的自定义包名，留空表示使用官方预设

    Returns:
        str: 实际生效的包名
    """
    preset = resolve_package_name(client_type, custom_package)
    custom = str(custom_package or '').strip()

    path = os.path.join(maa_path, 'resource', 'config.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning(f'未找到 MAA 资源文件 {path}，跳过包名写入')
        _log_effective(client_type, preset, custom)
        return preset
    except Exception as e:
        logger.warning(f'无法读取 MAA 资源文件 {path}：{e}，跳过包名写入')
        _log_effective(client_type, preset, custom)
        return preset

    packages = data.get('packageName', None)
    if not isinstance(packages, dict) or not packages:
        logger.warning(f'{path} 中的 packageName 缺失或格式异常，跳过包名写入')
        _log_effective(client_type, preset, custom)
        return preset

    old = packages.get(client_type, None)
    old = old.strip() if isinstance(old, str) else ''

    if not custom and old and old != preset:
        logger.warning(
            f'{path} 中 packageName.{client_type} 仍是 {old}，与预设 {preset} 不同，将继续沿用该值'
        )
        logger.warning(
            '该项不是本插件写入的。若这就是你的客户端，请把它填入「MAA 设置 - 自定义包名」；'
            f'若要恢复官方包名，请清空该项并手动改回 {preset}'
        )
        logger.info(f'MAA 客户端类型 {client_type} 沿用配置文件中的包名 {old}')
        return old

    if old == preset:
        _log_effective(client_type, preset, custom)
        return preset

    # 只改这一个键，其余键与键顺序保持原样
    packages[client_type] = preset
    tmp = f'{path}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logger.exception(e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        logger.critical(f'无法写入 MAA 资源包名映射，自定义包名不会生效：{path}')
        logger.critical(f'请手动把 packageName.{client_type} 改为 {preset}，或以管理员身份运行')
        raise RequestHumanTakeover

    logger.info(f'已更新 {path} 的 packageName.{client_type}: {old or "空"} -> {preset}')
    _log_effective(client_type, preset, custom)
    return preset


def _log_effective(client_type, package, custom):
    if custom:
        logger.info(f'MAA 客户端类型 {client_type} 使用自定义包名 {package}')
    else:
        logger.info(f'MAA 客户端类型 {client_type} 使用预设包名 {package}')
