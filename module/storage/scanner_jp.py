# -*- coding: utf-8 -*-
"""日服仓库材料页的名称匹配。

日服名条有两点与国服不同，不能直接复用 ``scanner.match_whitelist()``：

1. 名称是日文，且客户端会把读起来超过 5 个字的道具名截断成「..」，
   完整名称拿不到，只能按前缀匹配；
2. 同名多档的道具（兵装実験報告書 / メンタルユニット / 五类部件）
   截断后文字完全一样，必须靠框色或图标底色裁决。

匹配结果一律映射回国服白名单名，所以 GUI 展示、历史记录、
“较上次变化”的跨服对比都不受影响。
"""
import re

# 半浊音/浊音/小字/长音的折叠表。名条 OCR 实测会把「パーツ」读成
# 「パツ」「ハツ」「パ一ツ」，把「ユニット」读成「ュニッ」，折叠后
# 这些读法归一；片假名之间的区分度不受影响（拗音/促音仍可辨）。
_FOLD = str.maketrans({
    'パ': 'ハ', 'バ': 'ハ', 'プ': 'フ', 'ブ': 'フ', 'ペ': 'ヘ', 'ベ': 'ヘ',
    'ポ': 'ホ', 'ボ': 'ホ',
    'ガ': 'カ', 'ギ': 'キ', 'グ': 'ク', 'ゲ': 'ケ', 'ゴ': 'コ',
    'ザ': 'サ', 'ジ': 'シ', 'ズ': 'ス', 'ゼ': 'セ', 'ゾ': 'ソ',
    'ダ': 'タ', 'ヂ': 'チ', 'ヅ': 'ツ', 'デ': 'テ', 'ド': 'ト',
    'ヴ': 'ウ', 'ァ': 'ア', 'ィ': 'イ', 'ゥ': 'ウ', 'ェ': 'エ', 'ォ': 'オ',
    'ッ': 'ツ', 'ャ': 'ヤ', 'ュ': 'ユ', 'ョ': 'ヨ',
    'ー': '', '一': '',
})

# 名条上可读的日服前缀 -> 国服白名单名。
# 前缀取自实机冻结截图（doc/storage-shots-jp/），客户端在 5 个字左右截断。
JP_NAMES = [
    ('開発用艦砲', '舰炮研发图'),
    ('開発用魚雷', '鱼雷研发图'),
    ('開発用対空', '防空炮研发图'),
    ('開発用艦載', '舰载机研发图'),
    ('開発用兵装', '装备研发图'),
    ('特装型兵装', '特装型突破'),
    ('試作型兵装', '试作型突破'),
    ('ネコハコSSR', '喵箱SSR型'),
]

# 五类部件：日服名是「汎用/艦砲/魚雷/対空砲/艦載機 + パーツ + 档位」，
# 档位数字被截断，只能按框色裁决——与国服同一条规则：
# T4 金框，T1 灰框、T2 蓝框、T3 紫框。
JP_PART_HEADS = [
    ('汎用パーツ', '通用部件'),
    ('艦砲パーツ', '主炮部件'),
    ('魚雷パーツ', '鱼雷部件'),
    ('対空砲パーツ', '防空炮部件'),
    ('艦載機パーツ', '舰载机部件'),
]

# 兵装実験報告書：绝密/机密两档在日服名条上完全同名（都截断成
# 「兵装実験報..」），按框色裁决。国服实测绝密是紫框、机密是金框。
JP_REPORT_QUALITY = {
    'purple': '绝密实验计划',
    'gold': '机密实验计划',
}

# メンタルユニット：心智单元与心智单元II 在日服名条上都截断成
# 「メンタルユ..」，两档同为金框，只能看图标底色——浅蓝是心智单元、
# 深蓝是心智单元II。实测图标中心区平均 G 分别为 198 与 168，取其间隔中值。
JP_CHIP_GREEN = 183
JP_CHIP_SAMPLE = (40, 100, 25, 100)  # 相对图标左上角 (y0, y1, x0, x1)


def normalize_jp(text):
    """日服名条 OCR 归一：去尾缀符号、折叠半浊音与长音。

    Args:
        text (str): OCR 原始结果，如 '汎用パツ..'

    Returns:
        str: 归一后的前缀，如 '汎用ハツ'
    """
    text = (text or '').strip()
    text = re.sub(r'[.\s|、,，:：·…]+$', '', text)
    text = text.replace('‖', 'II').replace('|', 'I')
    return text.translate(_FOLD)


def prefix_hit(text, alias):
    """归一后的名条文字是否与别名互为前缀。

    只允许“读短了”，不允许“读错了”：OCR 截断（'強化ユ' 之于
    '強化ユニット'）算命中，读成别的字（'SR' 之于 'SSR'）不算。
    这样 ネコハコSR 不会被误判成 ネコハコSSR。

    Args:
        text (str): normalize_jp 之后的 OCR 结果
        alias (str): 别名原文

    Returns:
        bool
    """
    alias = normalize_jp(alias)
    size = min(len(text), len(alias))
    if size < 3:
        return False
    return text[:size] == alias[:size]


def _match_unique(text, names):
    """在候选表里取唯一命中项，多个候选同样像时返回 None。

    Args:
        text (str): normalize_jp 之后的 OCR 结果
        names (list[(str, str)]): (别名, 国服名)

    Returns:
        str: 国服名；无命中或有歧义返回 None
    """
    hits = [(len(normalize_jp(alias)), canon)
            for alias, canon in names if prefix_hit(text, alias)]
    if not hits:
        return None
    best = max(size for size, _ in hits)
    top = {canon for size, canon in hits if size == best}
    if len(top) > 1:
        # 「開発用艦砲」与「開発用艦載」只差被截断的最后一个字，
        # 名条读到 4 个字时两者都像，此时宁可不记也不猜
        return None
    return top.pop()


def chip_tier(image, x, icon_top):
    """按图标底色区分心智单元与心智单元II。

    两档在日服名条上同名（都截断成「メンタルユ..」）且同为金框，
    图标是同一套图的浅蓝/深蓝两个版本，只能靠底色裁决。

    Args:
        image (np.ndarray): 截图
        x (int): 格子左上角 x
        icon_top (int): 图标区顶 y

    Returns:
        str: '心智单元' 或 '心智单元II'；取不到图标返回 None
    """
    if image is None:
        return None
    y0, y1, x0, x1 = JP_CHIP_SAMPLE
    icon = image[icon_top + y0:icon_top + y1, x + x0:x + x1]
    if icon.size == 0:
        return None
    green = float(icon[..., 1].mean())
    return '心智单元II' if green < JP_CHIP_GREEN else '心智单元'


def match_whitelist_jp(name, quality=None, image=None, x=0, icon_top=0):
    """把日服名条文字匹配回国服白名单名。

    Args:
        name (str): 名条 OCR 原始结果
        quality (str): 该格品质 'gold'/'purple'/'other'，零件与报告书靠它分档
        image (np.ndarray): 截图，心智单元靠图标底色分档
        x (int): 格子左上角 x
        icon_top (int): 图标区顶 y

    Returns:
        str: 国服白名单名，未匹配返回 None
    """
    text = normalize_jp(name)
    if len(text) < 3:
        return None
    # 同名多档：文字裁决不了，先走品质/图标
    if prefix_hit(text, '兵装実験報'):
        return JP_REPORT_QUALITY.get(quality)
    if prefix_hit(text, 'メンタル'):
        return chip_tier(image, x, icon_top)
    # 五类部件：档位数字被截断，只认金框（T4），其余档位一律不记
    for alias, head in JP_PART_HEADS:
        if prefix_hit(text, alias):
            return head + 'T4' if quality == 'gold' else None
    return _match_unique(text, JP_NAMES)
