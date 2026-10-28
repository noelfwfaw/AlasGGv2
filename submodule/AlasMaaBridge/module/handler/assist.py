"""
魔改客户端的启动辅助。

部分魔改客户端（如带「灵动岛」悬浮窗的版本）在进入游戏后会弹出一层辅助面板：
声明页 →（辅助密码）→ 功能面板。这套面板会挡住 MAA 的识别区域，所以在
「开始唤醒」之后先把它关掉。

用户已开启客户端的「彻底隐藏灵动岛(自动开音量键菜单)」：灵动岛完全消失、
不留热区（误触遮挡从根源消除），因此改为用音量键开关面板：
音量+ 打开 → 同意条款 → 进入辅助 → 音量- 关闭。
不再点击灵动岛，也不再进设置页点「隐藏灵动岛」——那既点不到（没有热区），
还会把用户刚改好的「彻底隐藏」设置改回去。

实现上不依赖主模块的 Device（MAA 插件的设备层是 FakeDevice），
直接用 adbutils 的 `screencap` + `input tap/swipe/keyevent` 完成截图与操作。
"""
import os
import random
import re
import time

import cv2
import numpy as np

from module.base.timer import Timer
from module.logger import logger
from submodule.AlasMaaBridge.module.device.emulator import MaaEmulator

ASSETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'assets', 'assist'
)

# 音量键（KEYCODE_VOLUME_UP / KEYCODE_VOLUME_DOWN）。魔改包的
# 「彻底隐藏灵动岛(自动开音量键菜单)」用它们开关辅助面板。
KEY_VOLUME_UP = 24
KEY_VOLUME_DOWN = 25
KEY_BACK = 4  # KEYCODE_BACK，前台被切走时退一格回游戏

# 三张模板素材的文件名。点击位置由 matchTemplate 的 max_loc 反推，客户端改版
# 不需要重测坐标（曾经写死的坐标在 2026-10-27 改版时失效过一次，已废弃）。
TEMPLATE_NAMES = ('assist_agree', 'assist_enter', 'assist_panel')

# 点击前后画面差异的均值阈值。连续两次截图（不点击）实测噪声约 0.03，
# 点开面板约 34，这里取 3 足够区分「这一下没生效」与「开了」。
CHANGE_THRESHOLD = 3.0

# 按键后先等系统音量条自动收起再截图。音量条本身就是一次全屏可见的变化，
# 不先等它消失，changed() 会把「音量条弹出」误判成「面板已打开」，
# 而且音量条压在模板区上会让分数偏低。
VOLUME_UI_WAIT = 3.0


def imread_gray(path):
    """
    读入灰度图。

    Windows 下 cv2.imread 不支持非 ASCII 路径（本仓库运行目录可能含中文，
    如 D:/360极速浏览器X下载/...），会静默返回 None；改走 np.fromfile + imdecode。

    Returns:
        np.ndarray | None:
    """
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except Exception as e:
        logger.info(e)
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


def _read_stream(stream):
    """
    adbutils 0.11 的 read_until_close() 会按 utf-8 解码，二进制截图不能用，
    这里直接读 socket。
    """
    conn = getattr(stream, 'conn', stream)
    conn.settimeout(10)
    data = b''
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _decode_screencap(data):
    """
    `screencap` 输出的是 12 字节头 + RGBA 像素，末尾还有一段全 0 的附加数据，
    宽高从头部读，像素从尾部倒着取。
    """
    header = np.frombuffer(data[0:12], dtype=np.uint32)
    width, height, _ = header
    image = np.frombuffer(data, dtype=np.uint8)
    image = image[-int(width * height * 4):].reshape(height, width, 4)
    return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)


class MaaAssist:
    """
    用音量键开关魔改客户端的启动辅助面板，并走完「同意条款 → 进入辅助」。
    """

    THRESHOLD = 0.85
    MAX_SWIPE = 4
    MAX_LOOPS = 40
    TIMEOUT = 150
    # 声明页一次滚到底部的滑动，参数来自实际截图
    SCROLL = (640, 600, 640, 300, 600)
    # 没有按钮时先空转一帧：点击后的转场中间帧同样没有按钮，
    # 直接当「声明页顶部」去滑动会把刚打开的页面滑走
    IDLE_BEFORE_SWIPE = 2

    def __init__(self, config, package=None):
        self.config = config
        self.emulator = MaaEmulator()
        self.serial = config.MaaEmulator_Serial
        self.templates = {}
        # 生效包名（含自定义覆盖）由 maa.py 传入，供前台校验；缺省时跳过校验
        self.package = package

    def run(self):
        """
        Returns:
            bool: True 表示已确认辅助面板关闭（或本就无需处理）
        """
        if not self.config.MaaAssist_Enable:
            logger.info('辅助启动: disabled')
            return False

        missing = []
        for name in TEMPLATE_NAMES:
            file = os.path.join(ASSETS, name + '.png')
            image = imread_gray(file)
            if image is None:
                # 区分「文件不存在」与「文件在但读不出来」，后者常见于非 ASCII 路径
                missing.append(file + ('（文件存在但无法解码）' if os.path.exists(file) else ''))
                continue
            self.templates[name] = image
        if missing:
            # 一次报全：逐个 return 会让用户补一个跑一次
            logger.warning(f'辅助启动: 缺少模板 {len(missing)} 个：' + '、'.join(missing))
            logger.warning(
                f'请确认 {ASSETS} 下有 {len(TEMPLATE_NAMES)} 个 png，且路径不含无法读取的字符'
            )
            return False

        try:
            return self._run()
        except Exception as e:
            logger.warning(f'辅助启动: failed {e}')
            # 异常退出同样要把面板关掉（音量-），避免面板留着遮挡 MAA
            try:
                self.ensure_closed()
            except Exception as e2:
                logger.warning(f'辅助启动: 收尾关闭面板失败 {e2}')
            return False

    def _run(self):
        timer = Timer(self.TIMEOUT).start()
        swipe_times = 0
        idle = 0

        # 打开：先判断面板是不是已经开着。若魔改包实现成 toggle（面板开着时
        # 按音量+会关闭），直接发音量+ 会适得其反，已开就跳过这一步。
        image = self.screenshot()
        score = self.scores(image)
        self.log_scores(score)
        if self.panel_open(score):
            logger.info('辅助启动: 面板已打开，跳过音量+')
        else:
            after = self.press_volume(KEY_VOLUME_UP)
            score = self.scores(after)
            self.log_scores(score)
            # 声明页顶部只有正文（同意按钮在页底），模板一个都匹配不上，
            # 所以「按钮出现」或「画面变了」都算打开成功
            if not (self.panel_visible(score) or self.changed(image, after)):
                logger.warning('辅助启动: failed（音量+后未检测到辅助面板）')
                self.save_failure(after)
                # 音量+ 可能已经生效、只是本帧判据没认出来，收尾时再确认一次
                self.ensure_closed()
                return False

        for _ in range(self.MAX_LOOPS):
            if timer.reached():
                logger.warning(f'辅助启动: skipped（处理超时 {self.TIMEOUT}s）')
                self.ensure_closed()
                return False

            image = self.screenshot()
            score = self.scores(image)
            self.log_scores(score)

            if score['assist_agree'] >= self.THRESHOLD:
                logger.info(f'AGREE score={score["assist_agree"]:.3f}')
                self.click('assist_agree', image)
                # 换页了，滑动额度重新给这一段用
                swipe_times = 0
                idle = 0
                continue
            if score['assist_enter'] >= self.THRESHOLD:
                logger.info(f'ENTER_ASSIST score={score["assist_enter"]:.3f}')
                self.click('assist_enter', image)
                # 已进入辅助，用音量-关闭面板
                return self.close_panel()
            if score['assist_panel'] >= self.THRESHOLD:
                # 客户端已记住声明/密码时，音量+ 可能直接进到功能面板
                logger.info(f'ALREADY_IN_ASSIST score={score["assist_panel"]:.3f}')
                return self.close_panel()

            # 一个按钮都没有：声明页顶部（正文在上、同意按钮在页底）或转场中。
            # 转场帧不能当声明页滑，先空转一帧等稳定
            idle += 1
            if idle < self.IDLE_BEFORE_SWIPE:
                continue
            if swipe_times >= self.MAX_SWIPE:
                logger.warning('辅助启动: failed（声明页滑动 4 次仍未找到按钮）')
                self.save_failure(image)
                self.ensure_closed()
                return False
            swipe_times += 1
            logger.info(f'声明页向下滚动 {swipe_times}/{self.MAX_SWIPE}')
            self.swipe(*self.SCROLL)

        logger.warning(f'辅助启动: failed（超过 {self.MAX_LOOPS} 轮）')
        self.ensure_closed()
        return False

    def close_panel(self):
        """
        进入辅助后用音量-关闭面板。

        完成判据：按键后重新截图，agree / enter / panel 全部不命中。
        必须带上 panel：只判 agree / enter 时，功能面板还开着也会被判成「已关闭」，
        于是带着挡住 MAA 的面板返回 True。一次没关掉时补发一次音量-。

        Returns:
            bool: True 表示面板已关闭且前台确认安全（或无法校验）
        """
        after = self.press_volume(KEY_VOLUME_DOWN)
        score = self.scores(after)
        self.log_scores(score)
        if not self.panel_open(score):
            logger.info('辅助启动: 已完成（音量关闭辅助面板）')
            return self.ensure_game_foreground(after)

        logger.info('辅助启动: 面板仍在，补发一次音量-')
        after = self.press_volume(KEY_VOLUME_DOWN)
        score = self.scores(after)
        self.log_scores(score)
        if not self.panel_open(score):
            logger.info('辅助启动: 已完成（音量关闭辅助面板）')
            return self.ensure_game_foreground(after)

        logger.warning('辅助启动: failed（音量-后面板仍未关闭）')
        self.save_failure(after)
        return False

    def _foreground_package(self):
        """取当前前台应用的包名，adb 异常或解析失败一律返回 None。"""
        try:
            output = self.emulator.shell(self.serial, 'dumpsys window | grep -E "mCurrentFocus|mFocusedApp"', timeout=6)
            # Window{<hash> u0 <包名>/<Activity>}，ActivityRecord 同构；
            # 必须非贪婪并限制字符集，greedy 会跨行把另一行的包名错配进来
            match = re.search(r'mCurrentFocus=Window\{[^}]*?\s([^\s/}]+)/', output)
            if match is None:
                match = re.search(r'mFocusedApp=.*?ActivityRecord\{[^}]*?\s([^\s/}]+)/', output)
            return match.group(1) if match else None
        except Exception:
            return None

    def ensure_game_foreground(self, failure_image):
        """
        面板关闭后正面确认前台还是游戏：音量- 关掉面板后，系统回到的是任务栈
        里的上一个 App，可能是浏览器，「按钮全消失」的旧判据会把切走误判成关闭成功。

        Args:
            failure_image (np.ndarray): 进入本方法时的画面，恢复失败时存档
        Returns:
            bool: True 表示已确认安全（或无法校验），False 表示没搞定
        """
        if not self.package:
            logger.info('辅助启动: 包名未知，跳过前台校验')
            return True
        foreground = self._foreground_package()
        if foreground is None:
            logger.info('辅助启动: 无法获取前台应用，跳过前台校验')
            return True
        if foreground == self.package:
            logger.info(f'辅助启动: 前台确认 {foreground} （游戏）')
            return True

        logger.warning(f'辅助启动: 面板已关闭但前台是 {foreground}，不是游戏 {self.package}')
        # 只补一次 BACK：退一格语义最轻，monkey/am start 会重建 task 把 Unity 拉回闪屏
        self.emulator.shell(self.serial, f'input keyevent {KEY_BACK}')
        time.sleep(1.5)
        foreground = self._foreground_package()
        if foreground == self.package:
            logger.info(f'辅助启动: 已用返回键回到游戏 {foreground}')
            return True
        if foreground is None:
            logger.info('辅助启动: 返回键后无法获取前台应用，跳过前台校验')
            return True
        self.save_failure(failure_image)
        logger.warning('辅助启动: 前台既不是面板也不是游戏，已保存现场截图')
        return False

    def ensure_closed(self):
        """
        失败/超时分支返回前确认面板已关闭，必要时补发音量-。
        """
        if not self.templates:
            return
        score = self.scores()
        if not self.panel_open(score):
            return
        logger.info('辅助启动: 失败收尾，补发音量- 关闭面板')
        self.press_volume(KEY_VOLUME_DOWN)

    def screenshot(self):
        """
        Returns:
            np.ndarray: BGR 图像，分辨率 1280x720
        """
        client = MaaEmulator.adb_client()
        stream = client.shell(self.serial, 'screencap', stream=True)
        try:
            data = _read_stream(stream)
        finally:
            try:
                stream.close()
            except Exception:
                pass

        # adb shell 可能把 0x0a 转成 0x0d0a，逐一尝试还原
        for transform in (
            lambda x: x,
            lambda x: x.replace(b'\r\n', b'\n'),
            lambda x: x.replace(b'\r\r\n', b'\n'),
        ):
            try:
                image = _decode_screencap(transform(data))
            except Exception:
                continue
            if image.shape[:2] != (720, 1280):
                logger.warning(f'辅助启动: 分辨率 {image.shape[:2]} 不是 1280x720，跳过')
                raise RuntimeError('unsupported resolution')
            return image

        raise RuntimeError('无法解码截图')

    def match(self, image, name):
        """匹配模板，返回 (分数, (x, y))，分数为归一化相关系数 0~1。"""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        result = cv2.matchTemplate(gray, self.templates[name], cv2.TM_CCOEFF_NORMED)
        # minMaxLoc 返回四元组，第二个是 max_val，第四个是 max_loc
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        return float(max_val), max_loc

    def scores(self, image=None):
        """
        Args:
            image (np.ndarray): BGR 图像，不传则重新截图

        Returns:
            dict: 所有模板的匹配分数
        """
        if image is None:
            image = self.screenshot()
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return {name: self.match(gray, name)[0] for name in self.templates}

    def changed(self, before, after):
        """
        点击前后画面是否有变化，用来判断这一下点击有没有生效。

        Returns:
            bool:
        """
        a = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY).astype(np.float32)
        b = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY).astype(np.float32)
        return float(np.abs(a - b).mean()) > CHANGE_THRESHOLD

    def log_scores(self, score):
        """
        每轮把模板分数都打出来，失败时才能判断是阈值问题还是页面变了。
        """
        logger.info(
            'scores: agree={:.2f} enter={:.2f} panel={:.2f}'
            .format(
                score['assist_agree'], score['assist_enter'], score['assist_panel'],
            )
        )

    def save_failure(self, image):
        """
        失败时保存现场截图。
        """
        folder = os.path.join(os.getcwd(), 'log')
        if not os.path.isdir(folder):
            # 部署时可能没有 log/，退到插件根目录
            folder = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(folder, 'assist_fail_{}.png'.format(time.strftime('%H%M%S')))
        try:
            # 走 imencode + tofile，避免中文路径下 imwrite 静默失败
            result, buffer = cv2.imencode('.png', image)
            if not result:
                logger.warning('辅助启动: 现场截图编码失败')
                return
            buffer.tofile(path)
            logger.info(f'辅助启动: 现场截图 {path}')
        except Exception as e:
            logger.warning(f'辅助启动: 保存现场截图失败 {e}')

    def panel_visible(self, score):
        """
        声明页 / 功能面板上的按钮是否出现（只看 agree / enter）。

        注意声明页顶部（只有正文时）一个按钮都匹配不上，此时用 changed() 判断。

        Returns:
            bool:
        """
        return (
            score['assist_agree'] >= self.THRESHOLD
            or score['assist_enter'] >= self.THRESHOLD
        )

    def panel_open(self, score):
        """
        面板是否已经开着：agree / enter / panel 任一命中。

        用于流程开始时判断要不要发音量+（toggle 实现下会反而关闭面板）。

        Returns:
            bool:
        """
        return self.panel_visible(score) or score['assist_panel'] >= self.THRESHOLD

    def click(self, name, image):
        """点击当前帧里模板的最佳匹配位置，判定与点击同源，界面挪了也跟着走。"""
        score, (x, y) = self.match(image, name)
        if score < self.THRESHOLD:
            logger.warning(f'辅助启动: {name} score={score:.3f} 低于阈值，跳过点击')
            return False
        h, w = self.templates[name].shape
        # 中心点附近 ±4px 抖动，避免每次都点同一个像素
        x += w // 2 + random.randint(-4, 4)
        y += h // 2 + random.randint(-4, 4)
        return self.tap(x, y)

    def press_volume(self, keycode):
        """
        发送一次音量键（走与 tap/swipe 相同的 MaaEmulator.shell 通道）。

        Args:
            keycode (int): KEY_VOLUME_UP / KEY_VOLUME_DOWN

        Returns:
            np.ndarray: 按键后稳定下来的画面
        """
        self.emulator.shell(self.serial, f'input keyevent {keycode}')
        # 等系统音量条收起，否则返回的画面里带着音量条，会干扰判据
        time.sleep(VOLUME_UI_WAIT)
        return self.wait_stable()

    def tap(self, x, y):
        """
        Returns:
            np.ndarray: 点击后稳定下来的画面
        """
        self.emulator.shell(self.serial, f'input tap {x} {y}')
        return self.wait_stable()

    def swipe(self, x1, y1, x2, y2, duration):
        self.emulator.shell(self.serial, f'input swipe {x1} {y1} {x2} {y2} {duration}')
        self.wait_stable()

    def wait_stable(self, timeout=6.0):
        """
        等画面稳定再返回。

        点击后页面有转场动画，太早截图会拿到「一个按钮都没有」的中间帧，
        状态机会误以为需要滑动，从而把刚打开的设置页滑走。
        """
        image = self.screenshot()
        timer = Timer(timeout).start()
        while not timer.reached():
            time.sleep(0.4)
            cur = self.screenshot()
            if not self.changed(image, cur):
                return cur
            image = cur
        return image
