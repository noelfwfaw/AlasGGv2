# -*- coding: utf-8 -*-
"""
日服外观装备统计离线回放（不连设备、不进游戏）。

用法：
    python dev_tools/gear_skin_replay_jp.py           # 全部断言，退出码 0 = 全绿
    python dev_tools/gear_skin_replay_jp.py --diag    # 打印逐帧读数与槽位

素材：doc/gearskin-shots-jp/ 下的冻结截图（只读，禁止修改），取自日服实机
（jp-15，1280x720，活动「光闇の夜想曲」的装備外装箱）：
    00_exchange.png   兑换弹窗（箱名、詳細、戻る）
    p00_000 ~ p00_011 信息弹窗的 12 个采样帧，位置与运行时 Scroll.set 完全一致

滚动位置为什么写死而不用 INFO_SCROLL.cal_position：滚动条静止一会儿会淡出，
淡出时 cal_position 读到的是假值（length=0），运行时的 scroll_to() 此时退回
「请求值」。下面的 POSITIONS 就是采集那 12 帧时运行日志里的实际读数，等价于
运行时用的值。
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
# OCR 模型按 ./bin/cnocr_models/ 相对路径加载，必须切到仓库根
os.chdir(REPO_ROOT)

import cv2
import numpy as np

import module.config.server as server
from module.gear_skin.assets import EXPECTED_ROW_SIZES, EXPECTED_SLOTS
from module.gear_skin.category import skin_category
from module.gear_skin.gear_skin import GearSkinScanner

SHOT_DIR = os.path.join(REPO_ROOT, 'doc', 'gearskin-shots-jp')

# 12 个采样帧与其滚动位置（0.0 顶部 ~ 1.0 底部），取自实跑日志
FRAMES = (
    ('p00_000.png', 0.000), ('p00_001.png', 0.205), ('p00_002.png', 0.399),
    ('p00_003.png', 0.578), ('p00_004.png', 0.847), ('p00_005.png', 0.989),
    ('p00_006.png', 0.079), ('p00_007.png', 0.282), ('p00_008.png', 0.432),
    ('p00_009.png', 0.733), ('p00_010.png', 0.921), ('p00_011.png', 0.989),
)

# 数量真值（按槽位顺序，实机画面逐格核对过：把行放大 4 倍读数）。
# 名称不做全量真值 —— 日服 13px 假名的 OCR 有稳定误差，写死会把误差当正确。
AMOUNT_TRUTH = [9, 10, 5, 11, 12,
                7, 5, 10, 10, 11,
                10, 7, 7, 4, 11,
                10, 9, 8]

# OCR 稳定读对的名称，作为名称回归的锚点
MUST_READ = ('聖中の双羽根弾', '紋魔の宝石弾', '染羽の切先魚雷', '燭台の灯火艦載機')

# 日服名称的合法结尾
NAME_SUFFIXES = ('弾', '魚雷', '砲', '艦載機')
# 三架同型飞机按固定顺序补的后缀
PLANE_SUFFIXES = ('（魚雷）', '（戦闘）', '（爆撃）')

BOX_NAME = '装備外装箱(光闇の夜想曲)'


class FakeDevice:
    """只提供 image 的最小设备桩，供 read_frame_rows 使用。"""

    def __init__(self, image):
        self.image = image


def load_shot(name):
    path = os.path.join(SHOT_DIR, name)
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError('failed to load %s' % path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def build_scanner(image=None):
    """绕过设备初始化，手工装配只读部分。"""
    scanner = GearSkinScanner.__new__(GearSkinScanner)
    scanner.device = FakeDevice(image)
    return scanner


def collect(diag=False):
    """
    按运行时的做法把 12 帧拼成一屏。

    Returns:
        tuple[list[tuple[str, int]], bool]: (槽位列表, 是否读全 18 个)
    """
    frames = []
    for shot, position in FRAMES:
        image = load_shot(shot)
        scanner = build_scanner(image)
        rows = scanner.read_frame_rows()
        if diag:
            print('  %-14s p=%.3f %s' % (shot, position,
                                        [[v[0] for _, v in sorted(c.items())] for _, c in rows]))
        frames.extend((y + position * 405, cells) for y, cells in rows)

    scanner = build_scanner()
    rows = scanner.group_rows(frames)
    slots = scanner.rows_to_slots(rows)
    if diag:
        print('  行结构 %s（期望 %s）' % ([len(r) for r in rows], list(EXPECTED_ROW_SIZES)))
    complete = len(slots) == EXPECTED_SLOTS
    if slots:
        slots = scanner.apply_plane_variants(slots)
    if diag:
        for name, amount in slots:
            print('    %-26s %s' % (name, amount))
    return slots, complete


def strip_plane_suffix(name):
    """
    去掉飞机型号后缀，取回基名。

    MUST_READ 里写的是基名（燭台の灯火艦載機），而槽位里的三条是同名 + 后缀。

    Args:
        name (str):

    Returns:
        str:
    """
    for suffix in PLANE_SUFFIXES:
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def test_slots(diag=False):
    """槽位数、行结构、数量序列、名称合法性与飞机型号后缀。"""
    ok = True
    slots, complete = collect(diag=diag)
    if len(slots) != EXPECTED_SLOTS:
        print('    BAD  槽位数 %d != %d' % (len(slots), EXPECTED_SLOTS))
        return False
    if not complete:
        print('    BAD  未读全 %d 个槽位' % EXPECTED_SLOTS)
        ok = False

    amounts = [amount for _, amount in slots]
    if amounts != AMOUNT_TRUTH:
        print('    BAD  数量序列 %s != %s' % (amounts, AMOUNT_TRUTH))
        ok = False

    for name, _ in slots:
        if not any(strip_plane_suffix(name).endswith(suffix) for suffix in NAME_SUFFIXES):
            print('    BAD  名称结尾不合法 %r' % name)
            ok = False

    # 最后 9 格是 3 组同名飞机，每组必须补齐 魚雷/戦闘/爆撃
    planes = [name for name, _ in slots[-9:]]
    for index in range(0, 9, 3):
        group = planes[index:index + 3]
        expected = [group[0][:-len(PLANE_SUFFIXES[0])] + suffix
                    for suffix in PLANE_SUFFIXES]
        if group != expected:
            print('    BAD  飞机型号后缀 %s != %s' % (group, expected))
            ok = False

    names = {strip_plane_suffix(name) for name, _ in slots}
    for name in MUST_READ:
        if name not in names:
            print('    MISS %s' % name)
            ok = False
    return ok


def test_box_name():
    """兑换弹窗：箱名（括号常被 OCR 丢掉，要能补回来）与两个按钮。"""
    image = load_shot('00_exchange.png')
    scanner = build_scanner(image)
    ok = True
    title = scanner.ocr_title.ocr(image)
    box_name = scanner.lang.normalize_box_name(title)
    print('  %-18s title=%r -> %r' % ('00_exchange.png', title, box_name))
    if box_name != BOX_NAME:
        print('    BAD  箱名 %r != %r' % (box_name, BOX_NAME))
        ok = False
    if scanner.lang.box_keyword not in title:
        print('    BAD  标题里没有关键词 %r' % scanner.lang.box_keyword)
        ok = False
    if scanner.lang.detail_text not in scanner.ocr_detail.ocr(image):
        print('    BAD  读不到「%s」按钮' % scanner.lang.detail_text)
        ok = False
    if scanner.lang.cancel_text not in scanner.ocr_cancel.ocr(image):
        print('    BAD  读不到「%s」按钮' % scanner.lang.cancel_text)
        ok = False
    return ok


def test_category():
    """WebUI 着色分类要能认出日服后缀，且飞机型号后缀仍分类正确。"""
    cases = [
        ('聖中の双羽根弾', 'bullet'),
        ('恋光の矢弾魚雷', 'torpedo'),
        ('魔蜘媒の糸砲弾', 'shell'),
        ('祝福の夫使碍砲', 'shell'),
        ('燭台の灯火艦載機（魚雷）', 'plane_torpedo'),
        ('燭台の灯火艦載機（戦闘）', 'plane_fighter'),
        ('燭台の灯火艦載機（爆撃）', 'plane_bomber'),
    ]
    ok = True
    for name, expect in cases:
        got = skin_category(name)
        if got != expect:
            print('    BAD  %-24s 分类 %s != %s' % (name, got, expect))
            ok = False
    print('  日服后缀分类 %d 例 -> %s' % (len(cases), 'ok' if ok else 'bad'))
    return ok


def test_names():
    """
    名称清洗的单元断言（不依赖截图）。

    这一组对应两个实机踩过的坑：
      1. 前导噪声正则曾按「非汉字一律剥掉」写，日服整条假名的名称
         （レイイムネームル弾）会被切空、整格丢弃
      2. 后缀被读成形近字（魚需 / 艦載挨）时，后缀回退修不回来
    """
    from module.gear_skin.gear_skin import NAME_NOISE_PATTERN

    scanner = build_scanner()
    lang = scanner.lang
    ok = True

    # 假名名称不能被噪声正则吃掉，前导标点要被剥掉
    cases = [
        ('，レイイムネームル弾', 'レイイムネームル弾'),
        ('レイイムネームル弾', 'レイイムネームル弾'),
        ('シルトライデン弾', 'シルトライデン弾'),
        ('聖中の双羽根弾', '聖中の双羽根弾'),
        ('，火焰球弾', '火焰球弾'),
    ]
    for text, expect in cases:
        got = NAME_NOISE_PATTERN.sub('', text)
        if got != expect:
            print('    BAD  噪声清洗 %r -> %r != %r' % (text, got, expect))
            ok = False

    # 带拉丁字母的读数要判废（G載機 是 艦載機 的 2x 误识）
    for text in ('G載機', 'v幽冥弾'):
        if lang.is_name(text):
            print('    BAD  %r 被判成合法名称' % text)
            ok = False
    for text in ('聖中の双羽根弾', 'レイイムネームル弾', '燭台の灯火艦載機'):
        if not lang.is_name(text):
            print('    BAD  %r 被判成非法名称' % text)
            ok = False

    # 形近字修正
    fixes = [('強襲の応千魚需', '強襲の応千魚雷'),
             ('魔影の円環艦載挨', '魔影の円環艦載機'),
             ('魔影の円環艦載械', '魔影の円環艦載機')]
    for text, expect in fixes:
        got = lang.clean_name(text)
        if got != expect:
            print('    BAD  形近修正 %r -> %r != %r' % (text, got, expect))
            ok = False

    # 已有合法后缀的名称不得被改动
    for text in ('聖中の双羽根弾', '恋光の矢弾魚雷', '祝福の夫使碍砲', '燭台の灯火艦載機'):
        if lang.clean_name(text) != text:
            print('    BAD  %r 被误改' % text)
            ok = False

    # 字数：汉字 + 假名都算（否则 聖中の双羽根弾 比不出长短）
    if lang.name_letters('聖中の双羽根弾') <= lang.name_letters('聖中弾'):
        print('    BAD  name_letters 没把假名算进去')
        ok = False

    print('  名称清洗 %d + %d + %d 例 -> %s'
          % (len(cases), len(fixes), 4, 'ok' if ok else 'bad'))
    return ok


def main():
    server.server = 'jp'
    diag = '--diag' in sys.argv
    results = [('slots', test_slots(diag=diag)),
               ('names', test_names()),
               ('box_name', test_box_name()),
               ('category', test_category())]
    failed = [name for name, passed in results if not passed]
    if failed:
        print('FAILED: %s' % ', '.join(failed))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
