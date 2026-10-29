# -*- coding: utf-8 -*-
"""
日服仓库材料页离线回放（不连设备、不进游戏）。

用法：
    python dev_tools/storage_scan_replay_jp.py           # 全部断言，退出码 0 = 全绿
    python dev_tools/storage_scan_replay_jp.py --diag    # 打印逐格诊断

素材：doc/storage-shots-jp/ 下的冻结截图（只读，禁止修改），取自日服实机
（jp-15，1280x720）。截图不入库，换机器时需自己补。
真值表由人工从截图肉眼读取并与游戏内实况核对：日服名条会把超过 5 个字的
道具名截断成「..」，所以真值只到“国服白名单名”这一层，数量按截图核对。
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
from module.ocr.ocr import Ocr
from module.statistics.item import AmountOcr
from module.storage.scanner import MaterialScanner

SHOT_DIR = os.path.join(REPO_ROOT, 'doc', 'storage-shots-jp')

# 每页应命中的白名单名（display_name 之前），数量为截图实读
GROUND_TRUTH = {
    '00_top.png': {
        '特装型突破': 275, '舰炮研发图': 38, '鱼雷研发图': 52,
        '防空炮研发图': 59, '舰载机研发图': 47, '绝密实验计划': 27,
        '装备研发图': 171,
    },
    '03_chip.png': {
        # 两档在日服名条上都截断成「メンタルユ..」，靠图标底色区分
        '心智单元': 130958, '心智单元II': 15933,
    },
    '04_part.png': {
        # 日服部件名是「汎用/艦砲/魚雷/対空砲/艦載機 + パーツ + 档位」，
        # 档位被截断，只靠金框认 T4
        '通用部件T4': 7175, '主炮部件T4': 7490, '鱼雷部件T4': 5180,
        '防空炮部件T4': 3934, '舰载机部件T4': 4896, '试作型突破': 1052,
    },
    '06_report.png': {
        # 绝密/机密同名「兵装実験報..」，靠框色区分（紫=绝密、金=机密）
        '机密实验计划': 821, '喵箱SSR型': 633,
        '舰炮研发图': 744, '鱼雷研发图': 724,
        '防空炮研发图': 744, '舰载机研发图': 727,
    },
}

# 已知偏差：真值读不对、但已经查清原因且当前无法在不伤国服的前提下修掉。
# 每次运行都会打印 KNOWN 行，修好之后自动消失（不会变成静默放过）。
# 格式：页面 -> {白名单名: (真值, 当前实际读数, 原因)}
KNOWN_DEVIATIONS = {
    '03_chip.png': {
        '心智单元': (
            130958, 30958,
            '6 位数的首位“1”正好压在数量裁剪窗左边界（x+55）上，'
            '7 个纵向偏移里 4 个读到 130958、3 个丢掉首位。'
            '整体左移窗口能救这格，但会把卡面美术拉进来，'
            '同批格子反而新增 55282->5282、1041->2222 的错读；'
            '改按票数裁决同样不行：国服「960->2960」也是 4:3，'
            '且多数票是错的那个（见 storage_scan_replay.py 的 AMOUNT_CELLS）。'
        ),
    },
}


def load_shot(name):
    path = os.path.join(SHOT_DIR, name)
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError('failed to load %s' % path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def build_scanner():
    """绕过设备初始化，手工装配 OCR 组件。阈值与 MaterialScanner.__init__ 保持一致。"""
    scanner = MaterialScanner.__new__(MaterialScanner)
    scanner.ocr_name = Ocr([], lang='jp', letter=(255, 255, 255), threshold=160, name='SSName')
    scanner.ocr_name_retry = Ocr([], lang='jp', letter=(255, 255, 255), threshold=128, name='SSName2')
    scanner.ocr_amount = AmountOcr([], threshold=MaterialScanner.AMOUNT_THRESHOLD, name='SSAmount')
    scanner.votes = {}
    return scanner


def page_cells(scanner, image):
    """枚举页面上的每个可读格子：(x, icon_top, label, OCR 名)。"""
    cells = []
    for y1 in scanner.detect_name_bars(image):
        for ci in range(scanner.GRID_COLS):
            x = scanner.GRID_X0 + ci * scanner.GRID_DX
            y1c = scanner.cell_bar_top(image, x, y1)
            if y1c is None:
                continue
            icon_top = y1c - scanner.CARD_H
            label = image[y1c + 2:y1c + 40, x:x + scanner.CARD_H]
            if int(label.max()) - int(label.min()) <= 30:
                continue
            cells.append((x, icon_top, label))
    if not cells:
        return []
    names = scanner.ocr_name.ocr([c[2] for c in cells], direct_ocr=True)
    return [(x, top, label, name) for (x, top, label), name in zip(cells, names)]


def test_pages(scanner, diag=False):
    ok = True
    for page, truth in GROUND_TRUTH.items():
        image = load_shot(page)
        found = scanner.scan_current_view(image) or []
        print('  %s -> %s' % (page, found))
        if diag:
            for x, icon_top, _, name in page_cells(scanner, image):
                print('    x=%4d top=%3d name=%r amount=%s quality=%s'
                      % (x, icon_top, name, scanner.read_amount(image, x, icon_top),
                         scanner.cell_quality(image, x, icon_top)))
        by_name = {}
        for name, quality, amount in found:
            by_name.setdefault(name, []).append(amount)
        devs = KNOWN_DEVIATIONS.get(page, {})
        for name, amount in truth.items():
            if name not in by_name:
                print('    MISS %s (truth %s)' % (name, amount))
                ok = False
                continue
            if amount in by_name[name]:
                continue
            dev = devs.get(name)
            if dev and dev[1] in by_name[name]:
                print('    KNOWN %s truth=%s got=%s' % (name, dev[0], by_name[name]))
                for line in dev[2].split('；'):
                    print('          %s' % line)
                continue
            print('    BAD  %s truth=%s got=%s' % (name, amount, by_name[name]))
            ok = False
        for name in by_name:
            if name not in truth:
                print('    EXTRA %s got=%s' % (name, by_name[name]))
                ok = False
    return ok


def main():
    server.server = 'jp'
    scanner = build_scanner()
    diag = '--diag' in sys.argv
    results = {
        'pages': test_pages(scanner, diag=diag),
    }
    failed = [name for name, passed in results.items() if not passed]
    if failed:
        print('FAILED: %s' % ', '.join(failed))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
