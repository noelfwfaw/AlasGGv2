"""
外观装备分类与展示配色。

名称后缀按服务器的写法都认（见 language.py）：
  国服 子弹 / 鱼雷 / 炮弹 / 飞机
  日服 弾 / 魚雷 / 砲(砲弾) / 艦載機
每箱的三组同名飞机在采集阶段已补上型号后缀（国服 鱼雷/战斗/轰炸，
日服 魚雷/戦闘/爆撃），它们属于六类中的三类独立类型。

分类只用于 WebUI 着色，不影响统计口径；同一份记录里可能同时存在两种
服务器的写法（换服后历史记录仍保留），所以判定不区分服务器。
"""

# 分类键 -> 颜色。六类色相互不相同，且对白底对比度都在 4.3:1 以上
# （WebUI 是浅色主题，颜色太亮会看不清）。
SKIN_COLORS = {
    'bullet': '#C45C00',         # 子弹 - 橙
    'torpedo': '#1565C0',        # 鱼雷 - 蓝
    'shell': '#8E44AD',          # 炮弹 - 紫
    'plane_torpedo': '#00807F',  # 飞机（鱼雷）- 青
    'plane_fighter': '#C2185B',  # 飞机（战斗）- 玫红
    'plane_bomber': '#2E7D32',   # 飞机（轰炸）- 墨绿
    'plane': '#455A64',          # 旧记录里没有型号后缀的飞机 - 蓝灰
}
# 认不出类型时用中性色，不猜
SKIN_COLOR_DEFAULT = '#4A4A4A'

# 展示顺序：图例与分类判定都按这个顺序
SKIN_CATEGORY_ORDER = (
    'bullet', 'torpedo', 'shell',
    'plane_torpedo', 'plane_fighter', 'plane_bomber',
)

# 型号后缀 -> 分类（全角/半角括号都认，OCR 与人工编辑都可能出现半角；
# 日服用 魚雷/戦闘/爆撃，与国服的 鱼雷/战斗/轰炸 并存）
PLANE_VARIANT_CATEGORY = {
    '（鱼雷）': 'plane_torpedo',
    '(鱼雷)': 'plane_torpedo',
    '（魚雷）': 'plane_torpedo',
    '(魚雷)': 'plane_torpedo',
    '（战斗）': 'plane_fighter',
    '(战斗)': 'plane_fighter',
    '（戦闘）': 'plane_fighter',
    '(戦闘)': 'plane_fighter',
    '（轰炸）': 'plane_bomber',
    '(轰炸)': 'plane_bomber',
    '（爆撃）': 'plane_bomber',
    '(爆撃)': 'plane_bomber',
}

# 分类键 -> i18n 词条名（Gui.Statistics.<name>），供 WebUI 图例使用
SKIN_CATEGORY_I18N = {
    'bullet': 'GearSkinBullet',
    'torpedo': 'GearSkinTorpedo',
    'shell': 'GearSkinShell',
    'plane_torpedo': 'GearSkinPlaneTorpedo',
    'plane_fighter': 'GearSkinPlaneFighter',
    'plane_bomber': 'GearSkinPlaneBomber',
}


def skin_category(name):
    """
    判断外观装备属于哪一类。

    飞机必须排在鱼雷/炮弹之前判断：飞机名带型号后缀时形如
    「星环魔影飞机（鱼雷）」，直接按结尾匹配会先命中「鱼雷」。

    Args:
        name (str): 外观装备名，例如「星环魔影飞机（战斗）」

    Returns:
        str | None: SKIN_COLORS 的键；认不出返回 None
    """
    text = (name or '').replace(' ', '').strip()
    if not text:
        return None

    # 飞机：国服「…飞机」、日服「…艦載機」；末字偶尔被 OCR 漏识成「…飞」，
    # 也照样归到飞机。必须在鱼雷/炮弹之前判，否则 星环魔影飞机（鱼雷）
    # 会先命中「鱼雷」。
    if ('飞机' in text or '艦載機' in text
            or text.endswith('机') or text.endswith('飞') or text.endswith('機')):
        for suffix, category in PLANE_VARIANT_CATEGORY.items():
            if text.endswith(suffix):
                return category
        return 'plane'

    # 炮弹：多数叫「…炮弹」，但也有「…啾炮」这种同族写法（实测
    # 祝福天使啾炮 / 霉运恶魔啾炮 就是炮弹那一类），所以按「炮」判；
    # 日服写作「…砲」「…砲弾」。
    if '炮弹' in text or '砲弾' in text or text.endswith('炮') or text.endswith('砲'):
        return 'shell'
    if '子弹' in text:
        return 'bullet'
    if '鱼雷' in text or '魚雷' in text:
        return 'torpedo'
    # 兜底：后缀被 OCR 弄脏（例如「峭目子弹一」）时，多数仍能以「弹」收尾，
    # 而「炮弹」已在上面判掉，剩下的只可能是子弹；日服同理按「弾」收尾
    if text.endswith('弹') or text.endswith('弾'):
        return 'bullet'
    return None


def skin_color(name):
    """
    取外观装备名的展示颜色。

    Args:
        name (str): 外观装备名

    Returns:
        str: CSS 颜色
    """
    return SKIN_COLORS.get(skin_category(name), SKIN_COLOR_DEFAULT)
