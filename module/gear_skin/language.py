"""
按服务器选择外观装备统计用的文字与命名规则。

几何常量（assets.py）两服实测一致：信息弹窗的列位置、行距、名称与拥有数的
文字颜色都相同（日服实机 1280x720 逐项核对过），差别全在「文字」上——
UI 关键词、名称字符集、名称后缀、飞机型号后缀。

日服实机（jp-15）实测：
  - 兑换弹窗：按钮是「詳細」（国服「查看详情」），取消是「戻る」，
    箱名「装備外装箱(光闇の夜想曲)」，标题区与国服同一块区域就能读全
  - 信息弹窗：标题「情報 INFORMATION」，拥有数条带是「所持中:N」
  - 名称形如 聖中の双羽根弾 / 恋光の矢弾魚雷 / 魔蜘蝶の糸砲弾 / 魔影の円環艦載機
    （片假名少见，主要是汉字 + 平假名「の」）

新增服务器时继承 GearSkinLanguage 覆写属性即可，不必改 gear_skin.py。
"""

# 国服：名称是纯汉字
_CJK_START = '\u4e00'
_CJK_END = '\u9fa5'


def _is_cjk(char):
    """
    Args:
        char (str): 单个字符

    Returns:
        bool: 是否汉字
    """
    return _CJK_START <= char <= _CJK_END


def _is_kana(char):
    """
    平假名 + 片假名 + 长音符 + 汉字叠字符。日服名称里的「の」「ー」「々」
    都属于这一类，不认会把 聖中の双羽根弾 整条丢掉。

    Args:
        char (str): 单个字符

    Returns:
        bool: 是否假名类字符
    """
    if '\u3041' <= char <= '\u3096':
        return True
    if '\u30a1' <= char <= '\u30fa':
        return True
    return char in ('\u30fc', '\u3005')


class GearSkinLanguage:
    """国服的文字与命名规则（默认值）。"""

    #: 名称与 UI 文字用的 OCR 模型
    ocr_lang = 'cnocr'

    #: 兑换弹窗「查看详情」按钮文字
    detail_text = '查看详情'
    #: 箱名关键词，用于从兑换弹窗标题里切出系列名
    box_keyword = '备箱'
    #: 箱名统一写成「<box_label>(<系列名>)」
    box_label = '外观装备箱'
    #: 兑换弹窗「取消」按钮文字
    cancel_text = '取消'
    #: 信息弹窗标题文字（另有一条 INFORMATION 的公共兜底）
    info_title_texts = ('信息',)

    #: 名称的合法结尾（外观装备只有这四类）
    suffixes = ('子弹', '鱼雷', '炮弹', '飞机')
    #: 飞机类名称的标志字符
    plane_markers = ('机', '飞')
    #: 同名的三架飞机按固定顺序补的型号后缀
    plane_variant_suffixes = ('（鱼雷）', '（战斗）', '（轰炸）')
    #: 三架飞机是否必须「名称完全相同」才认定为一组（国服 OCR 稳定，按相等分组）
    plane_variant_exact_match = True
    #: 允许出现在名称里的非汉字符号（名称噪声过滤会放行）
    extra_chars = ()
    #: 后缀位置上的形近误识 -> 正确字（各服 OCR 模型不同，由子类提供）
    char_fixes = {}

    @staticmethod
    def name_letters(text):
        """
        名称里的有效字符个数，用于比较 2x / 3x 两次 OCR 谁读得更全。

        Args:
            text (str):

        Returns:
            int:
        """
        return sum(1 for char in text if _is_cjk(char))

    def is_name(self, text):
        """
        名称是否可信（用来挡掉低质量帧的误识）。

        国服的外观装备名全是汉字，出现拉丁字母/数字/符号说明这一帧的图像
        质量有问题（实测读出 v幽冥子弹），整格丢弃让别的采样点来决定。

        Args:
            text (str):

        Returns:
            bool:
        """
        if len(text) < 2:
            return False
        return all(_is_cjk(char) or char in self.extra_chars for char in text)

    def is_plane(self, name):
        """
        名称是否是飞机类外观装备。

        正常情况下以「机」结尾；OCR 偶发漏掉末字时只剩「…飞」。

        Args:
            name (str):

        Returns:
            bool:
        """
        return name.endswith(self.plane_markers)

    def fix_tail(self, name):
        """
        把名称末尾两字里的形近误识换成正确的字，换完以合法后缀结尾才采用。

        OCR 把「雷」读成「需」、「機」读成「挨/械」这类错误，字符本身是合法
        汉字，`is_name` 拦不住、后缀回退也修不回来（实测 強襲の応千魚需
        应该是 …魚雷）。只动末尾两字，名称主体不动。

        Args:
            name (str):

        Returns:
            str:
        """
        if not self.char_fixes or len(name) < 2:
            return name
        fixed = name[:-2] + ''.join(self.char_fixes.get(char, char) for char in name[-2:])
        if fixed != name and fixed.endswith(self.suffixes):
            return fixed
        return name

    def clean_name(self, name):
        """
        用「名称必定以 suffixes 结尾」纠正名称。

        OCR 偶尔会凭空多输一个末字（实测 峭目子弹 -> 峭目子弹一），这类错误
        不会被槽位计数发现，只能靠命名后缀回退修正。回退后仍对不上就原样
        返回，不丢数据。

        Args:
            name (str):

        Returns:
            str:
        """
        if name.endswith(self.suffixes):
            return name
        for cut in (1, 2):
            if len(name) - cut >= 2 and name[:-cut].endswith(self.suffixes):
                return name[:-cut]
        return self.fix_tail(name)

    def repair_plane_name(self, name):
        """
        补齐飞机名的末字。

        国服 OCR 偶发漏掉「机」只剩「…飞」，补回来否则同一栏会出现两种写法。

        Args:
            name (str):

        Returns:
            str:
        """
        if name.endswith('飞'):
            return name + '机'
        return self.fix_tail(name)

    def normalize_box_name(self, title):
        """
        清洗箱名 OCR 结果，统一写成「<box_label>(<系列名>)」。

        括号笔画细，OCR 经常读不出来甚至把右括号读成 J（实测
        「装備外装箱光闇の夜想曲」「…夜想曲J」），所以不依赖括号定位，
        而是按关键词切前缀、再去掉两端的括号类字符。

        Args:
            title (str): 兑换弹窗标题的 OCR 结果

        Returns:
            str: 例 外观装备箱(光暗夜想曲)
        """
        text = (title or '').replace(' ', '').replace('\u3000', '').strip()
        tail = text
        index = tail.find(self.box_keyword)
        if index >= 0:
            tail = tail[index + len(self.box_keyword):]
        tail = tail.strip('（）()［］[]<>').rstrip('JjIil|')
        if len(tail) >= 2:
            return '{}({})'.format(self.box_label, tail)
        return text


class GearSkinLanguageJP(GearSkinLanguage):
    """日服的文字与命名规则。"""

    ocr_lang = 'jp'

    detail_text = '詳細'
    box_keyword = '外装箱'
    box_label = '装備外装箱'
    cancel_text = '戻る'
    info_title_texts = ('情報',)

    # 日服名称以 弾 / 魚雷 / 砲 / 砲弾 / 艦載機 结尾，实测同一箱里都出现过。
    # 「砲弾」必须排在「弾」之前才能命中（清理只判结尾，顺序不影响结果，
    # 但 readability 上按长到短排）。
    suffixes = ('艦載機', '魚雷', '砲弾', '砲', '弾')
    plane_markers = ('機',)
    plane_variant_suffixes = ('（魚雷）', '（戦闘）', '（爆撃）')
    # 日服 OCR 对假名与「艦載機」这种复合词不稳，同一组三架飞机会读成三个
    # 略有差异的名字（魔影の円環艦載機 / 随影の環艦載挨 / 魔影の環艦機），
    # 按字符串相等分不出组，改为按「连续 3 格都是飞机」分块。
    plane_variant_exact_match = False
    # 日服名称里有「の」这类假名，还有极少数用「・」分隔的写法
    extra_chars = ('・',)
    # 13px 尺寸下 jp 模型的稳定形近误识，只用在名称末尾两字（后缀所在位置）：
    #   魚需 -> 魚雷、艦載挨 / 艦載械 -> 艦載機
    char_fixes = {'需': '雷', '挨': '機', '械': '機'}

    @staticmethod
    def name_letters(text):
        """
        Args:
            text (str):

        Returns:
            int: 汉字 + 假名的个数
        """
        return sum(1 for char in text if _is_cjk(char) or _is_kana(char))

    def is_plane(self, name):
        """
        日服的舰载机名固定含「艦載機」，OCR 常把末字读成 挨/械/機 之类，
        但「艦」基本能留住，所以按「含艦」判，比只看末字稳得多
        （实测 魔影の環艦載挨 / 祈願の翼窓艦機 都会被末字判据漏掉）。

        Args:
            name (str):

        Returns:
            bool:
        """
        return '艦' in name or name.endswith('機')

    def is_name(self, text):
        """
        日服名称是「汉字 + 假名」，不能要求全汉字（否则 聖中の双羽根弾 被丢）。

        Args:
            text (str):

        Returns:
            bool:
        """
        if len(text) < 2:
            return False
        return all(_is_cjk(char) or _is_kana(char) or char in self.extra_chars
                   for char in text)


def build_language(server_name):
    """
    取该服务器的文字规则。

    Args:
        server_name (str): module.config.server.server，'cn'/'en'/'jp'/'tw'

    Returns:
        GearSkinLanguage:
    """
    if server_name == 'jp':
        return GearSkinLanguageJP()
    return GearSkinLanguage()
