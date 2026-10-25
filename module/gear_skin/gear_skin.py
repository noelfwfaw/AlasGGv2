"""
活动外观装备统计。

随仓库扫描（StorageScan）任务一并执行：进入活动商店，打开外观装备箱的
「信息」弹窗，统计当期每种外观装备的拥有数量。

入口：
    GearSkinScanner(config, device).run(scan_time)

说明：
- 箱子的识别直接复用 module/shop_event 的规则（价格 2000、限购 10 == SkinBox），
  本模块不重新识别箱子、也不改动 module/shop_event 的任何行为。
- 每个箱子固定 18 件外观装备（4 行：5+5+5+3），按「槽位」逐条记录；同名的三架
  飞机（鱼雷 / 战斗 / 轰炸）是三种不同装备，不做同名合并，改为按固定顺序加后缀。
- 弹窗里的列表只能用右侧滚动条拖动，内容区 swipe 几乎无效（见 assets.py）。
  采集时把滚动条 set 到若干个归一化位置，每处读一屏，再按「滚动位置 + 行 y」
  归一到内容坐标，按行距吸附成行号，天然去重、天然不漏。
- 任何异常都在 run() 最外层吞掉（只 warning），不影响仓库扫描主流程。
"""
import os
import re
from collections import Counter
from datetime import datetime

import cv2

from module.base.button import Button
from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import color_similarity_2d, crop, extract_letters, save_image
from module.config.utils import server_time_offset
from module.gear_skin.assets import *
from module.gear_skin.records import append_record
from module.logger import logger
from module.ocr.ocr import Ocr
from module.shop.assets import NAV_EVENT, NAV_GENERAL, SHOP_BUY_CONFIRM, SHOP_CLICK_SAFE_AREA
from module.shop.ui import ShopUI
from module.shop_event.assets import NO_NAV_EVENT_CHECK
from module.shop_event.clerk import EventShopClerk, ItemNotFoundError
from module.shop_event.ui import EVENT_SHOP_SCROLL
from module.ui.scroll import Scroll
from module.ui.assets import SHOP_GOTO_MUNITIONS
from module.ui.page import page_munitions

OCR_EXCHANGE_DETAIL = Ocr(EXCHANGE_DETAIL_BUTTON, lang='cnocr', name='OCR_EXCHANGE_DETAIL')
OCR_EXCHANGE_TITLE = Ocr(EXCHANGE_TITLE_AREA, lang='cnocr', letter=(232, 200, 90),
                         name='OCR_EXCHANGE_TITLE')
OCR_EXCHANGE_CANCEL = Ocr(EXCHANGE_CANCEL, lang='cnocr', name='OCR_EXCHANGE_CANCEL')
OCR_INFO_TITLE = Ocr(INFO_TITLE_AREA, lang='cnocr', name='OCR_INFO_TITLE')

# 信息弹窗的滚动条。只有拖它才能稳定翻页。
INFO_SCROLL = Scroll(area=INFO_SCROLL_AREA, color=INFO_SCROLL_COLOR,
                     is_vertical=True, name='INFO_SCROLL')
INFO_SCROLL.color_threshold = INFO_SCROLL_COLOR_THRESHOLD

ERROR_FOLDER = os.path.join('log', 'storage_scan', 'err')


class GearSkinScanner(EventShopClerk, ShopUI):
    def __init__(self, config, device=None, task=None):
        super().__init__(config, device, task)

    @cached_property
    def ocr_model(self):
        return Ocr((0, 0, 1, 1), lang='cnocr').cnocr

    def run(self, scan_time):
        """
        Args:
            scan_time (str): 仓库扫描记录的时间串，原样写进统计记录

        Returns:
            dict | None: 写入的记录，跳过或失败时 None
        """
        try:
            return self._run(scan_time)
        except Exception as e:
            logger.warning(f'GearSkinStatistic failed: {e}')
            self.cleanup()
            return None

    def cleanup(self):
        """
        异常退出时把残留弹窗关掉，避免污染后续任务。

        只会点「取消」/「确定」，绝不点「兑换」。
        """
        try:
            self.close_all_popups(force=True)
            self._ensure_main()
        except Exception as e:
            logger.warning(f'Gear skin: cleanup failed: {e}')

    def close_all_popups(self, force=False):
        """
        关掉「信息」「兑换」两层弹窗（信息弹窗永远盖在兑换弹窗上）。

        判据只用「取消」：信息弹窗打开时它是读不到的，所以一旦读到就说明
        只剩兑换弹窗了。force=True 时对确定还开着的信息弹窗补一次「确定」。

        Args:
            force (bool): 确定信息弹窗还开着，OCR 判不出来也点一次「确定」
        """
        for attempt in range(6):
            self.device.screenshot()
            if '取消' in OCR_EXCHANGE_CANCEL.ocr(self.device.image):
                self.click_area(EXCHANGE_CANCEL)
                self.wait_stable()
                continue
            if force:
                force = False
                self.click_area(INFO_CONFIRM)
                self.wait_stable()
                continue
            break

    def wait_stable(self, timeout=1.5):
        """
        等画面稳定（连续两帧相同），避免对着旧帧做判断。
        """
        last = None
        for _ in self.loop(timeout=timeout):
            if last is not None and self.same_image(last, self.device.image):
                break
            last = self.device.image.copy()

    def _ensure_main(self):
        """
        回到主界面。停在不认识的页面（弹窗盖住界面）时先设法关掉弹窗。

        Returns:
            bool:
        """
        for attempt in range(3):
            try:
                self.ui_goto_main()
                return True
            except Exception as e:
                logger.warning(f'Gear skin: cannot go to main page: {type(e).__name__} {e}')
                self.close_all_popups(force=attempt >= 1)
        return False

    def _run(self, scan_time):
        if not self.enter_event_shop():
            return None

        count, navbar = self.event_shop_tab_count_and_navbar
        logger.info(f'Gear skin: {count} event shop tab(s)')
        boxes = []
        for index in range(count):
            navbar.set(main=self, left=index + 1)
            try:
                items = self.scan_all()
            except (ItemNotFoundError, IndexError) as e:
                logger.warning(f'Gear skin: scan event shop tab {index + 1} failed: {e}')
                continue

            skin_boxes = [item for item in items if item.name == 'SkinBox']
            if not skin_boxes:
                logger.info(f'No gear skin box in event shop tab {index + 1}')
                continue
            for order, item in enumerate(skin_boxes):
                box = self.scan_box(item, order=order)
                if box:
                    boxes.append(box)

        # 守卫三（内容层）：一个箱都没读到，不写空记录
        if not boxes:
            logger.info('No gear skin box found, skip gear skin statistics')
            return None

        slot_count = sum(box['slot_count'] for box in boxes)
        total = sum(skin['amount'] for box in boxes for skin in box['skins'])
        kinds = sum(1 for box in boxes for skin in box['skins'] if skin['amount'] > 0)
        expected = EXPECTED_SLOTS * len(boxes)
        complete = slot_count == expected
        if not complete:
            logger.warning(f'Gear skin: incomplete, {slot_count}/{expected} slots')
        record = {
            'time': scan_time,
            'boxes': [{'box': box['box'], 'skins': box['skins'],
                       'slot_count': box['slot_count'], 'complete': box['complete']}
                      for box in boxes],
            'slot_count': slot_count,
            'expected_slot_count': expected,
            'complete': complete,
            'total': total,
            'kinds': kinds,
        }
        append_record(self.config.config_name, record)
        logger.info(f'Gear skin: {slot_count}/{expected} slots, {kinds} kinds, total {total}')
        return record

    def enter_event_shop(self):
        """
        守卫一（导航层）与守卫二（时间层）。

        导航不能用 NAV_EVENT 判断「活动商店是否存在」——它的定义色是选中态
        颜色（实测未选中态 B 通道差 33 > 阈值 30），点击前必然判否。存在性
        判断用 NO_NAV_EVENT_CHECK，与 EventShop 任务一致。

        Returns:
            bool: False 表示非活动期间或页面异常，应跳过
        """
        # 从主界面重走，避免停在商店其它子页时 NAV_* 判定全部失配
        if not self._ensure_main():
            logger.info('Failed to go back to main page, skip gear skin statistics')
            return False
        self.ui_goto_shop()
        if not self._wait_appear(NAV_GENERAL, timeout=6):
            logger.info('Not in munitions page, skip gear skin statistics')
            return False
        if self.appear(NO_NAV_EVENT_CHECK, offset=(5, 5)):
            logger.info('No event shop currently, skip gear skin statistics')
            return False

        # 点进活动商店，最多重试 3 次（不用 ui_click：它要等 10s 才点第一下）
        for _ in range(3):
            if self.appear(NAV_EVENT, offset=(5, 5)):
                break
            self.device.click(NAV_EVENT)
            timer = Timer(2, count=4).start()
            while not timer.reached():
                self.device.screenshot()
                if self.appear(NAV_EVENT, offset=(5, 5)):
                    break
        else:
            logger.info('Failed to open event shop, skip gear skin statistics')
            return False

        # 活动商店页加载完成前 OCR 可能读到半截文字，重试到超时
        timer = Timer(4, count=8).start()
        deadline = None
        while 1:
            deadline = self._get_event_deadline()
            if deadline is not None:
                break
            if timer.reached():
                logger.info('Failed to read event shop deadline, skip gear skin statistics')
                return False
            self.device.screenshot()

        server_now = datetime.now() - server_time_offset()
        if deadline <= server_now:
            logger.info(f'Event shop ended at {deadline}, skip gear skin statistics')
            return False
        return True

    def _wait_appear(self, button, timeout=6):
        """
        等待某个按钮出现（页面切换有动画，单帧判定会误判）。

        Returns:
            bool:
        """
        timer = Timer(timeout, count=int(timeout / 0.5)).start()
        while 1:
            if self.appear(button, offset=(5, 5)):
                return True
            if timer.reached():
                return False
            self.device.screenshot()

    def scan_box(self, item, order=0):
        """
        打开一个外观装备箱的信息弹窗并统计。

        Args:
            item (EventShopItem): name == 'SkinBox' 的商店物品
            order (int): 该 tab 内第几个箱子，仅用于 OCR 失败时的兜底命名

        Returns:
            dict | None: {'box': 箱名, 'skins': [{'name','amount'}], 'slot_count': int}
        """
        if item.scroll_pos is not None:
            EVENT_SHOP_SCROLL.set(item.scroll_pos, main=self)
        try:
            items = self.event_shop_get_items()
        except ItemNotFoundError as e:
            logger.warning(f'Gear skin: box items not found: {e}')
            return None
        candidates = [i for i in items if i.name == 'SkinBox']
        if not candidates:
            logger.warning(f'Gear skin: no SkinBox at scroll {item.scroll_pos}')
            return None
        target = min(candidates, key=lambda x: abs(x.button[0] - item.button[0]))

        self.device.click(target)

        box_name = ''
        detail = False
        timer = Timer(3, count=6).start()
        for _ in self.loop():
            if self.abort_if_buying():
                return None
            title = OCR_EXCHANGE_TITLE.ocr(self.device.image)
            if '备箱' in title:
                box_name = self.normalize_box_name(title)
            if '查看详情' in OCR_EXCHANGE_DETAIL.ocr(self.device.image):
                detail = True
                break
            if timer.reached():
                break
        if not detail:
            logger.warning(f'Gear skin: no 查看详情 button for {box_name or item}')
            self.save_error()
            self.close_exchange()
            return None

        self.click_area(EXCHANGE_DETAIL_BUTTON)

        opened = False
        timer = Timer(3, count=6).start()
        for _ in self.loop():
            if self.abort_if_buying():
                return None
            text = OCR_INFO_TITLE.ocr(self.device.image)
            if '信息' in text or 'INFORMATION' in text.upper():
                opened = True
                break
            if timer.reached():
                break
        if not opened:
            logger.warning(f'Gear skin: info popup not opened for {box_name}')
            self.save_error()
            self.close_exchange()
            return None

        if not box_name:
            box_name = f'外观装备箱#{order + 1}'
        logger.hr(f'Gear skin: {box_name}', level=2)
        try:
            skins, complete = self.collect_skins()
        finally:
            # 无论采集是否异常，都要把「信息」「兑换」两层弹窗关掉
            self.close_all_popups(force=True)
        if not skins:
            logger.warning(f'Gear skin: no skin read from {box_name}')
            return None
        total = sum(skin['amount'] for skin in skins)
        logger.info(f'Gear skin: {box_name} {len(skins)} slots, total {total}')
        for skin in skins:
            logger.attr(skin['name'], skin['amount'])
        return {'box': box_name, 'skins': skins, 'slot_count': len(skins),
                'complete': complete}

    @staticmethod
    def normalize_box_name(title):
        """
        清洗箱名 OCR 结果，只保留「外观装备箱(系列名)」。

        Returns:
            str: 例 外观装备箱(光暗夜想曲)
        """
        text = title.replace(' ', '').strip()
        matched = re.search(r'备箱[(（]([^)）]*)', text)
        if matched:
            return f'外观装备箱({matched.group(1)})'
        matched = re.search(r'备箱([^)）]*)', text)
        if matched and len(matched.group(1)) >= 2:
            return f'外观装备箱({matched.group(1)})'
        return text

    # ------------------------------------------------------------------
    # 信息弹窗采集
    # ------------------------------------------------------------------
    def collect_skins(self):
        """
        采集信息弹窗的 18 个槽位。

        做法：把滚动条 set 到若干个归一化位置，每处读一屏；把每屏读到的行按
        「screen_y + position * INFO_SCROLL_DISTANCE」归一到内容坐标，再按行距
        吸附成行号。同一行在不同屏里归一到同一个 y（实测误差 < 1px），所以
        既不会重复也不会漏。

        Returns:
            tuple[list[dict], bool]: ([{'name','amount'}], 是否读全 18 槽位)
        """
        frames = self.sample_frames(INFO_SCAN_POSITIONS)
        rows = self.group_rows(frames)
        slots = self.rows_to_slots(rows)

        if len(slots) < EXPECTED_SLOTS:
            logger.warning(f'Gear skin: only {len(slots)} slots, resample with offset')
            extra = tuple(min(round(p + INFO_RETRY_POSITION_OFFSET, 3), 1.0)
                          for p in INFO_SCAN_POSITIONS)
            frames.extend(self.sample_frames(extra))
            rows = self.group_rows(frames)
            slots = self.rows_to_slots(rows)

        complete = len(slots) == EXPECTED_SLOTS
        if not complete:
            logger.warning(f'Gear skin: {len(slots)}/{EXPECTED_SLOTS} slots after resample')
        if slots:
            slots = self.apply_plane_variants(slots)
        return [{'name': name, 'amount': amount} for name, amount in slots], complete

    def sample_frames(self, positions):
        """
        逐个滚动位置采样一屏。

        Returns:
            list[tuple[float, dict]]: [(归一化 y, {列号: (名称, 拥有数)}), ...]
        """
        frames = []
        for position in positions:
            actual = self.scroll_to(position)
            if actual is None:
                continue
            rows = self.read_frame_rows()
            for y, cells in rows:
                frames.append((y + actual * INFO_SCROLL_DISTANCE, cells))
            logger.info(f'Gear skin: p={position:.2f} ({actual:.3f}) '
                        f'{[[v[0] for _, v in sorted(c.items())] for _, c in rows]}')
        return frames

    def scroll_to(self, position):
        """
        把信息弹窗的滚动条拖到指定归一化位置。

        device.swipe 的名字会被记进点击历史，同一名字攒到 12 次会抛
        GameTooManyClickError，所以每次拖拽前清空记录。

        注意「读不到滚动条位置」不等于「这次采样失败」：滚动条静止一会儿会
        淡出，此时 Scroll.cal_position 会返回假的 0.0（length=0）。拖拽本身
        是到位的（Scroll.set 内部循环校验过），所以读不到时退回请求值 ——
        位置误差 < 0.05，换算成内容偏移不到 20px，远小于半个行距，行号吸附
        仍然正确。早期版本在这里返回 None 丢掉整个采样点，导致 p=0.0 那一屏
        从来没被采到。

        Args:
            position (float): 0.0 顶部 ~ 1.0 底部

        Returns:
            float | None: 用于归一化的位置（实测优先，读不到退回请求值）；
                          拖拽抛异常时返回 None
        """
        self.device.click_record_clear()
        try:
            INFO_SCROLL.set(position, main=self, random_range=(0, 0),
                            skip_first_screenshot=False)
        except Exception as e:
            logger.warning(f'Gear skin: scroll to {position} failed: {type(e).__name__} {e}')
            return None

        # set 之后要等惯性动画停，滚动条读数才是稳定值。
        # 超时给到 2s 只是上限：画面连续两帧相同就会提前退出，正常约 0.6s。
        self.wait_stable(2.0)
        self.device.screenshot()

        for _ in range(3):
            if INFO_SCROLL.length:
                return INFO_SCROLL.cal_position(self)
            # 滚动条淡出导致 length=0，等一帧再看
            self.device.screenshot()

        logger.info(f'Gear skin: scrollbar not visible at {position}, use requested value')
        return position

    @staticmethod
    def group_rows(frames):
        """
        把各屏读到的行按归一化 y 聚成内容行。

        行距 153px，所以按 76px 半径聚类即可；同一行出现在多屏时逐列投票。

        Args:
            frames (list[tuple[float, dict]]): [(归一化 y, {列号: 单元格})]

        Returns:
            list[dict]: 每行 {列号: (名称, 拥有数)}，自上而下
        """
        groups = []
        for y, cells in sorted(frames, key=lambda item: item[0]):
            if groups and y - groups[-1]['y'] <= INFO_ROW_HEIGHT * 0.5:
                groups[-1]['votes'].append(cells)
            else:
                # 聚类锚点用该组的首个 y：归一化后同一行的抖动 < 2px，够用
                groups.append({'y': y, 'votes': [cells]})

        rows = []
        for group in groups:
            columns = set()
            for cells in group['votes']:
                columns.update(cells)
            row = {}
            for column in sorted(columns):
                candidates = [cells[column] for cells in group['votes'] if column in cells]
                voted = GearSkinScanner.vote_cell(candidates)
                if voted:
                    row[column] = voted
            rows.append(row)
        return rows

    @staticmethod
    def vote_cell(candidates):
        """
        同一格在多个屏里的读数投票。

        名称：优先取「另一个候选的末尾延长」——末字漏识（冰雪幽灵船飞 <- 冰雪
        幽灵船飞机）比多字常见，而多出来的字基本都是前导噪声（一纯白晨花子弹），
        所以只接受「长度 +1 且前缀相同」的延长，不接受任意更长的串。其余情况
        取众数，平票取短的。

        拥有数：在名称一致的前提下取众数；平票时取位数多的（OCR 漏识比多识
        常见，11 被读成 1 的情况远多于 1 被读成 11）。

        Args:
            candidates (list[tuple[str, int]]): [(名称, 拥有数)]

        Returns:
            tuple[str, int] | None:
        """
        if not candidates:
            return None
        names = Counter(name for name, _ in candidates)
        longer = [other for other in names for name in names
                  if other != name and len(other) == len(name) + 1
                  and other.startswith(name)]
        if longer:
            name = max(Counter(longer).items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        else:
            name = min(names.items(), key=lambda kv: (-kv[1], len(kv[0])))[0]
        amounts = Counter(amount for candidate_name, amount in candidates
                          if candidate_name == name)
        amount = max(amounts.items(), key=lambda kv: (kv[1], len(str(kv[0]))))[0]
        return name, int(amount)

    def rows_to_slots(self, rows):
        """
        把行字典压成扁平槽位列表。

        格数少于 3 的行几乎都是被视口切掉半截的残行，先丢掉；若总槽位仍超出
        预期（OCR 造出了假行），再从格数最少的行开始丢，直到回到 18 以内。

        Args:
            rows (list[dict]):

        Returns:
            list[tuple[str, int]]: [(名称, 拥有数)]
        """
        if not rows:
            return []
        # 只有 1 格的孤行几乎都是把 UI 装饰认成文字，直接丢
        kept = [(len(row), index, row) for index, row in enumerate(rows) if len(row) >= 2]
        sizes = [size for size, _, _ in kept]
        logger.info(f'Gear skin: {len(kept)} rows, sizes {sizes}')
        if len(sizes) == len(EXPECTED_ROW_SIZES) and tuple(sizes) != EXPECTED_ROW_SIZES:
            # 行数对但每行格数不对，说明某几格 OCR 失败（总数可能凑巧还是 18）
            logger.warning(f'Gear skin: row sizes {sizes} != expected '
                           f'{list(EXPECTED_ROW_SIZES)}')
        total = sum(sizes)
        if total > EXPECTED_SLOTS:
            # OCR 造出了假行：从格数最少的行开始丢，直到回到 18 以内
            kept.sort(key=lambda item: (item[0], item[1]))
            while sum(size for size, _, _ in kept) > EXPECTED_SLOTS and len(kept) > 1:
                dropped = kept.pop(0)
                logger.warning(f'Gear skin: drop odd row {dropped[1]} '
                               f'with {dropped[0]} cells')
            kept.sort(key=lambda item: item[1])

        slots = []
        for _, _, row in kept:
            slots.extend(row[column] for column in sorted(row))
        return slots

    def read_frame_rows(self):
        """
        读取当前帧所有可辨认的行。

        一格里「拥有:N」条带能找到、但上方名称读不出来，通常是该行被视口
        边缘截断；这种格子直接丢掉，由相邻屏补齐。

        Returns:
            list[tuple[float, dict]]: [(拥有数条带的 y, {列号: (名称, 拥有数)})]
        """
        image = self.device.image
        cells = []
        for index, x in enumerate(INFO_COLUMNS):
            x1 = x + INFO_COLUMN_OFFSET[0]
            x2 = x + INFO_COLUMN_OFFSET[1]
            area = (x1, INFO_LIST_AREA[1], x2, INFO_LIST_AREA[3])
            img = crop(image, area)
            name_bands = self.row_bands(color_similarity_2d(img, INFO_NAME_COLOR))
            amount_bands = self.row_bands(color_similarity_2d(img, INFO_AMOUNT_COLOR))
            for amount_band in amount_bands:
                amount_y = amount_band[0] + area[1]
                # 名称 band 取「拥有 N 上方 [2, 46]px 窗口内的全部 band」的并集，
                # 末字被切成独立 band 时也能带上
                window_top = amount_band[0] - INFO_NAME_WINDOW[1]
                window_bottom = amount_band[0] - INFO_NAME_WINDOW[0]
                paired = [b for b in name_bands
                          if b[1] > window_top and b[0] < window_bottom]
                if not paired:
                    continue
                # 主 band 取离「拥有 N」最近的一条；只把与它相距 6px 内的相邻
                # band 并进来（末字被切的情况），远处的噪声不参与，否则名称图
                # 被撑大后 OCR 会吐乱码
                main = max(paired, key=lambda b: b[1])
                merged = [b for b in paired
                          if b[1] >= main[0] - INFO_NAME_BAND_GAP
                          and b[0] <= main[1] + INFO_NAME_BAND_GAP]
                name_band = (min(b[0] for b in merged), max(b[1] for b in merged))
                name_top = name_band[0] + area[1]
                name_bottom = name_band[1] + area[1]
                # 名称被视口边缘切掉半截时 OCR 会给出乱码，必须按几何位置剔除
                if name_top < INFO_NAME_SAFE_TOP or name_bottom > INFO_NAME_SAFE_BOTTOM:
                    continue
                name = self.ocr_name(
                    crop(img, (0, name_band[0] - 2, img.shape[1], name_band[1] + 2))
                )
                name = self.clean_name(name)
                amount = self.ocr_amount(
                    crop(img, (0, amount_band[0] - 1, img.shape[1], amount_band[1] + 2))
                )
                # 名称必须是纯汉字：外观装备名都是「XX子弹 / XX鱼雷 / XX炮弹 /
                # XX飞机」这种中文，出现拉丁字母/数字/符号说明这一帧的图像质量
                # 有问题（实测读出 v幽冥子弹、一纯白晨花子弹，同帧的数字也会
                # 跟着错成 11 -> 1），整格丢弃，让别的采样点来决定
                if len(name) < 2 or not self.is_chinese_name(name) or amount is None:
                    continue
                cells.append((amount_y, index, name, amount))

        # 按 y 聚成行
        groups = []
        for cell in sorted(cells):
            if groups and cell[0] - groups[-1][0][0] <= INFO_ROW_TOLERANCE:
                groups[-1].append(cell)
            else:
                groups.append([cell])

        rows = []
        for group in groups:
            rows.append((group[0][0], {cell[1]: (cell[2], cell[3]) for cell in group}))
        return rows

    @staticmethod
    def apply_plane_variants(slots):
        """
        同名飞机按固定顺序补后缀：鱼雷 / 战斗 / 轰炸。

        列表里同名外观装备连续出现 3 次即为三种型号（顺序固定），不能合并成
        一条，否则拥有总数会被算错。注意「星环魔影飞机」的三个槽位是跨行的
        （第 2 行末列 + 第 3 行前两列），所以必须在扁平槽位序列上判断。

        Args:
            slots (list[tuple]): [(name, amount), ...]

        Returns:
            list[tuple]: [(name, amount), ...]
        """
        result = []
        index = 0
        while index < len(slots):
            end = index
            while end < len(slots) and slots[end][0] == slots[index][0]:
                end += 1
            run = end - index
            name = slots[index][0]
            if run == len(PLANE_VARIANT_SUFFIXES) and GearSkinScanner.is_plane(name):
                # 末字漏识成「…飞」时补回「…飞机」，否则同一栏会出现两种写法
                if name.endswith('飞'):
                    name = name + '机'
                for offset, (_, amount) in enumerate(slots[index:end]):
                    result.append((name + PLANE_VARIANT_SUFFIXES[offset], amount))
            else:
                result.extend(slots[index:end])
            index = end
        return result

    @staticmethod
    def is_plane(name):
        """
        名称是否是飞机类外观装备。

        正常情况下以「机」结尾；OCR 偶发漏掉末字时只剩「…飞」。

        Returns:
            bool:
        """
        return name.endswith('机') or name.endswith('飞')

    @staticmethod
    def row_bands(mask):
        """
        把颜色相似度图按行投影成文字条带。

        Returns:
            list[tuple]: [(y1, y2), ...]
        """
        rows = (mask >= INFO_MASK_THRESHOLD).sum(axis=1)
        bands = []
        start = None
        for y, count in enumerate(rows):
            if count >= INFO_BAND_MIN_PIXELS:
                if start is None:
                    start = y
                continue
            if start is not None:
                bands.append([start, y])
                start = None
        if start is not None:
            bands.append([start, len(rows)])

        merged = []
        for band in bands:
            if merged and band[0] - merged[-1][1] <= 2:
                merged[-1][1] = band[1]
            else:
                merged.append(band)
        return [tuple(b) for b in merged
                if INFO_BAND_HEIGHT[0] <= b[1] - b[0] <= INFO_BAND_HEIGHT[1]]

    def ocr_line(self, image, letter, threshold=128, scale=2):
        """
        单行 OCR。13px 小字先放大再识别。

        Returns:
            str:
        """
        gray = extract_letters(image, letter=letter, threshold=threshold)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        chars = self.ocr_model.atomic_ocr_for_single_lines([gray], None)
        if not chars:
            return ''
        return ''.join(chars[0]).strip()

    def ocr_name(self, image):
        """
        识别装备名称。

        2x 和 3x 各读一次，只有 3x 的汉字数「严格更多」时才采用 3x：
        cnocr 对 7 字长名（冰雪幽灵船飞机）在 2x 下容易漏末字，3x 通常能读全；
        但 3x 偶尔会把整行读成英文乱码（m-i / anal），用汉字数把关即可挡掉。
        前导标点噪声已由下面的正则清掉，所以「更长」在本函数里等价于「更全」。

        Returns:
            str:
        """
        best = ''
        for scale in (2, 3):
            text = self.ocr_line(image, INFO_NAME_COLOR, scale=scale).replace(' ', '')
            # 去掉 OCR 偶尔带出的前导标点（实测「，火焰球子弹」）
            text = re.sub(r'^[^\u4e00-\u9fa5A-Za-z0-9]+', '', text)
            if self.chinese_count(text) > self.chinese_count(best):
                best = text
        return best

    @staticmethod
    def chinese_count(text):
        """
        Returns:
            int: 字符串里的汉字个数
        """
        return sum(1 for char in text if '\u4e00' <= char <= '\u9fa5')

    @classmethod
    def is_chinese_name(cls, text):
        """
        外观装备名是否全是汉字（用来挡掉低质量帧的误识）。

        Returns:
            bool:
        """
        return len(text) >= 2 and cls.chinese_count(text) == len(text)

    @staticmethod
    def clean_name(name):
        """
        用「外观装备名必定以 子弹/鱼雷/炮弹/飞机 结尾」纠正名称。

        cnocr 偶尔会凭空多输一个末字（实测 峭目子弹 -> 峭目子弹一，像素分布
        证实图上只有 4 个字），这类错误不会被槽位计数发现，只能靠命名后缀
        回退修正。回退后仍对不上就原样返回，不丢数据。

        Returns:
            str:
        """
        if name.endswith(GEAR_SKIN_SUFFIXES):
            return name
        for cut in (1, 2):
            if len(name) - cut >= 2 and name[:-cut].endswith(GEAR_SKIN_SUFFIXES):
                return name[:-cut]
        return name

    @staticmethod
    def normalize_digits(text):
        """
        把 cnocr 常见的数字形近误识纠正回来。

        实测「拥有:1」「拥有:11」会稳定读成 `拥有:l`、`拥有:ll`、`拥有:l1`
        （小写 L 当 1）。不做这一步时：`拥有:ll` 匹配不到数字 → 整格被丢弃；
        `拥有:l1` 只匹配到后面的 1 → 11 变成 1。后一种错误不会被「18 槽位」
        的结构校验发现，是最危险的。

        外观装备名与「拥有」都是汉字，替换这些字符不会误伤。

        Args:
            text (str):

        Returns:
            str:
        """
        text = text.replace(' ', '')
        for source, target in (('l', '1'), ('I', '1'), ('|', '1'),
                               ('i', '1'), ('O', '0'), ('o', '0')):
            text = text.replace(source, target)
        return text

    def ocr_amount(self, image):
        """
        识别「拥有 N」里的数字。

        三个坑：
        1. 两位数中间常被识别出空格（`拥有:1 1`），直接 re.search 只会取到
           第一个 1，所以先去掉所有空格。
        2. 数字 1 会被认成小写 L（`拥有:l1`），由 normalize_digits 纠正。
        3. 单个数字笔画细，固定阈值/缩放常读不出，按三组参数各试一次取众数；
           平票取位数多的（漏识比多识常见）。

        Returns:
            int | None:
        """
        found = []
        for threshold, scale in ((128, 2), (128, 3), (96, 2)):
            text = self.ocr_line(image, INFO_AMOUNT_COLOR,
                                 threshold=threshold, scale=scale)
            matched = re.search(r'(\d+)', self.normalize_digits(text))
            if matched:
                found.append(int(matched.group(1)))
        if not found:
            return None
        counts = Counter(found)
        return max(counts.items(), key=lambda kv: (kv[1], len(str(kv[0]))))[0]

    @staticmethod
    def same_image(image1, image2):
        if image1 is None or image2 is None:
            return False
        return float(cv2.absdiff(image1, image2).mean()) < 1.0

    def click_area(self, area):
        self.device.click(Button(area, color=(255, 255, 255), button=area))

    def abort_if_buying(self):
        """
        误入购买流时立刻退出，禁止碰「兑换」按钮。

        只用 SHOP_BUY_CONFIRM（点击「兑换」后才会出现的二级确认对话框）判据：
        SHOP_BUY_CONFIRM_AMOUNT 的区域 (756,622,827,653) 正好落在兑换弹窗
        「兑换」按钮 ≈(727,608,890,662) 内部，用它判购买流会在正常兑换弹窗上
        误报，导致每个箱子都被中止。

        Returns:
            bool: True 表示已中止该箱
        """
        if self.appear(SHOP_BUY_CONFIRM, offset=(20, 40)):
            logger.warning('Gear skin: entered purchase flow, abort')
            self.device.click(SHOP_CLICK_SAFE_AREA)
            self.click_area(EXCHANGE_CANCEL)
            return True
        return False

    def close_info(self):
        self.click_area(INFO_CONFIRM)
        timer = Timer(3, count=6).start()
        for _ in self.loop():
            if '取消' in OCR_EXCHANGE_CANCEL.ocr(self.device.image):
                break
            if timer.reached():
                logger.warning('Gear skin: exchange popup not back after closing info')
                break
        self.close_exchange()

    def close_exchange(self):
        self.click_area(EXCHANGE_CANCEL)
        # 等兑换弹窗真的消失，避免残留弹窗污染下一个箱子的物品扫描
        timer = Timer(3, count=6).start()
        for _ in self.loop():
            if '取消' not in OCR_EXCHANGE_CANCEL.ocr(self.device.image):
                break
            if timer.reached():
                logger.warning('Gear skin: exchange popup still visible')
                break
        self.event_shop_handle_obstruct()

    def save_error(self):
        try:
            os.makedirs(ERROR_FOLDER, exist_ok=True)
            file = os.path.join(
                ERROR_FOLDER,
                'gear_skin_{}.png'.format(datetime.now().strftime('%Y%m%d_%H%M%S'))
            )
            save_image(self.device.image, file)
            logger.info(f'Gear skin: screenshot saved to {file}')
        except Exception as e:
            logger.warning(f'Gear skin: save screenshot failed {e}')
