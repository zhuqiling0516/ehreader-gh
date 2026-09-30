# -*- coding: utf-8 -*-
"""移动端漫画阅读器（安卓 APK / Windows 桌面预览）

界面（Kivy 实现，安卓打包见 buildozer.spec）：

    BrowseScreen   搜索 / 标签 / 分类筛选、收藏夹、最近打开、标签收藏、本地目录
    ReaderScreen   阅读器：单页（点击或滑动翻页、双击缩放、拖动平移）/ 连页
    SettingsScreen 代理、Cookie、本地目录、阅读方向、数据管理

数据层在 ehapi.py，直接复用桌面版的 pages.py / browse.py。
"""

from __future__ import annotations

import io
import sys
import bisect
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# vendor/：打包 APK 时构建脚本会把 pages.py / browse.py 复制进去
# 用 reversed 插入，保证 PATH_CANDIDATES 里靠前的目录最终优先级更高。
PATH_CANDIDATES = (BASE_DIR / 'vendor', BASE_DIR.parent, BASE_DIR)
for _candidate in reversed(PATH_CANDIDATES):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import Screen, ScreenManager, SlideTransition
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton


def _install_cjk_font():
    """把 Kivy 默认字体换成系统里的中文字体。

    Kivy 自带的 Roboto 没有中文字形，安装到安卓后中文会被渲染成一个个方框
    （看上去就是「乱码」）。这里在系统字体目录里找一个含中文的字体，用它覆盖
    Kivy 的默认字体名 'Roboto'；一个都找不到时保持原样，英文和数字仍然正常。
    """
    import os
    from kivy.core.text import LabelBase

    hints = ('DroidSansFallback', 'NotoSansCJK', 'NotoSansSC', 'NotoSansHans',
             'SourceHanSans', 'NotoSerifCJK')
    candidates = []
    for directory in ('/system/fonts', '/system/font', '/vendor/fonts'):
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        # .ttf 优先：.ttc 是字体集合，SDL_ttf 只会打开第一个 face，中文覆盖面可能不全
        for suffix in ('.ttf', '.otf', '.ttc'):
            for hint in hints:
                for name in names:
                    if hint.lower() in name.lower() and name.lower().endswith(suffix):
                        candidates.append(os.path.join(directory, name))
    # 桌面端跑同一套界面时的兜底
    candidates += [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyh.ttf',
                   r'C:\Windows\Fonts\simhei.ttf',
                   '/System/Library/Fonts/PingFang.ttc']
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            LabelBase.register(name='Roboto', fn_regular=path, fn_bold=path,
                               fn_italic=path, fn_bolditalic=path)
        except Exception:
            continue
        return path
    return None


CJK_FONT = _install_cjk_font()
if CJK_FONT:
    print('[ehreader] 中文字体: %s' % CJK_FONT)
else:
    print('[ehreader] 警告：没找到中文字体，中文会显示成方框')

# 调试开关：把窗口尺寸、控件坐标、每次触摸落点打到 logcat（adb logcat -s python）。
# 默认关闭 —— 它每次触摸都要写文件 + 走一遍控件树，是卡顿的来源之一。
# 需要时在设备上造个空文件即可打开，不用重新打包：
#     adb shell run-as org.ehreader.ehreader touch files/debug_ui
def _debug_flag() -> bool:
    try:
        import os
        return os.path.exists(os.path.join(
            os.environ.get('ANDROID_PRIVATE') or '.', 'debug_ui'))
    except Exception:                           # noqa: BLE001
        return False


DEBUG_UI = _debug_flag()
_DBG_PATH = None


def _dbg(*parts) -> None:
    """调试输出：同时打 stdout 和写设备文件。

    安卓上 p4a 的 stdout→logcat 重定向偶发失效（实测某一轮启动的 print 一条都没进
    logcat 缓冲区），所以再写一份到 files/dbg.log，用
        adb shell run-as org.ehreader.ehreader cat files/dbg.log
    就能读到，不再依赖 logcat。
    """
    global _DBG_PATH
    if not DEBUG_UI:
        return
    line = '[dbg] ' + ' '.join(str(p) for p in parts)
    print(line)
    try:
        import os
        if _DBG_PATH is None:
            _DBG_PATH = os.path.join(
                os.environ.get('ANDROID_PRIVATE') or '.', 'dbg.log')
        with open(_DBG_PATH, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except Exception:                           # noqa: BLE001
        pass

from browse import (CATEGORIES, DEFAULT_SELECTED_MASK, LANGUAGES, SOURCE_LABELS,
                    split_terms)
from ehapi import Library, Store, data_dir
from pages import is_online_url
from mobui import (THEME, AsyncTextureImage, GalleryRow, flat_input, make_label,
                   mark_scrolling, paint_round, set_upload_busy, toast)

BG = THEME['bg']
PANEL = THEME['panel']
ACCENT = THEME['accent']


def flat_button(text, on_press=None, width=None, height=dp(40), flex=False,
                accent=False, quiet=False, radius=None, **kwargs):
    """统一风格的按钮：扁平深色 + 圆角（不再用 Kivy 默认的灰底方角贴图）

    flex   : True 时横向占满剩余空间（size_hint_x=1），用于分段按钮
    accent : 主题色实心（主操作，如「搜索」）
    quiet  : 更暗的次级按钮
    """
    background = THEME['accent'] if accent else (
        THEME['accent_d'] if quiet else THEME['button'])
    button = Button(text=text, size_hint=((1 if flex else None), None),
                    size=(width or dp(64), height),
                    background_normal='', background_down='',
                    background_color=(0, 0, 0, 0),
                    color=THEME['text'], **kwargs)
    paint_round(button, background, radius)
    if on_press:
        def _fire(*_args):                      # 临时：确认按钮确实被按到
            _dbg('button fired:', text)
            on_press()
        button.bind(on_release=_fire)
    return button


class PageView(FloatLayout):
    """单页阅读视图：自适应缩放、双击放大、放大后拖动、点击/滑动翻页"""

    def __init__(self, screen, **kwargs):
        super().__init__(**kwargs)
        self.screen = screen
        self.zoom = 1.0
        self.offset = [0.0, 0.0]
        self.image = AsyncTextureImage(size_hint=(None, None))
        self.image.on_fail = self._on_image_fail
        self.add_widget(self.image)
        self._touch = None
        self._retry = None
        self.bind(pos=self._relayout, size=self._relayout)
        self.image.bind(texture=lambda *_: Clock.schedule_once(self._relayout, 0))

    def _on_image_fail(self, key) -> None:
        """自动重试也失败：给一个看得见的重试按钮，别让用户对着空白页发呆"""
        toast(self, '图片加载失败，请检查网络后点“重试”')
        if self._retry is not None:
            return
        button = flat_button('重试', self._retry_load, width=dp(110), accent=True)
        button.size_hint = (None, None)
        button.size = (dp(110), dp(40))
        button.pos = (self.x + (self.width - dp(110)) / 2,
                      self.y + (self.height - dp(40)) / 2)
        self._retry = button
        self.add_widget(button)

    def _retry_load(self) -> None:
        if self._retry is not None:
            self.remove_widget(self._retry)
            self._retry = None
        self.image.start(0)
        self._relayout()

    # -- 显示 ----------------------------------------------------------
    def show(self, page) -> None:
        fit_kind = self.screen.app.store.setting('fit', 'contain')
        fit = (int(self.width) or 720, int(self.height) or 1280,
               fit_kind if fit_kind in ('contain', 'width') else 'contain', 1.0)
        key = (str(page), fit)
        self.zoom = 1.0
        self.offset = [0.0, 0.0]
        if self._retry is not None:               # 换页时把上一页的重试按钮收掉
            self.remove_widget(self._retry)
            self._retry = None
        if self.image.key == key and self.image.texture is not None:
            # 同一页已经在显示（例如 on_size 触发的重复调用）：
            # 只重新摆位置，绝不重启加载 —— 否则 _token 递增会把在途结果作废，
            # 而作废的结果不入缓存，就会陷入「反复下载解码却永远贴不上图」的死循环。
            self._relayout()
            return
        self.image.key = key
        self.image.max_side = 2200
        self.image.loader = self._bytes_loader(page, fit)
        self.image.start()
        self._relayout()

    @staticmethod
    def _bytes_loader(page, fit):
        """取原图字节流。

        优先用 page.fetch_bytes()：p4a 里 Pillow 没编 libwebp，而站点正文图是 webp，
        走 page.fetch()（Pillow 解码）会直接抛 "WEBP support not installed" → 白页。
        原始字节交给 mobui.prepare_image（webp→SDL2，其它→Pillow）才是对的。
        """
        def load() -> bytes:
            getter = getattr(page, 'fetch_bytes', None)
            if getter is not None:
                data = getter()
                _dbg('bytes_loader: fetch_bytes -> %d bytes' % len(data))
                return data
            _dbg('bytes_loader: 回退 page.fetch(fit)（Pillow 路径，webp 会失败）')
            raw = page.fetch(fit)                  # 退路：老接口返回 PIL 图
            buffer = io.BytesIO()
            raw.save(buffer, 'PNG')
            return buffer.getvalue()
        return load

    # -- 布局 ----------------------------------------------------------
    def _relayout(self, *_args) -> None:
        if self._retry is not None:
            self._retry.pos = (self.x + (self.width - self._retry.width) / 2,
                               self.y + (self.height - self._retry.height) / 2)
        texture = self.image.texture
        if texture is None or not self.width or not self.height:
            _dbg('relayout: skip (texture=%s view=%sx%s)' % (
                texture is not None, round(self.width), round(self.height)))
            return
        ratio = texture.height / float(texture.width or 1)
        base_w = self.width
        base_h = base_w * ratio
        if base_h > self.height:                  # 高图按高度装下
            base_h = self.height
            base_w = base_h / ratio
        width = base_w * self.zoom
        height = base_h * self.zoom
        self.image.size = (width, height)
        center_x = self.x + self.width / 2 + self.offset[0]
        center_y = self.y + self.height / 2 + self.offset[1]
        self.image.pos = (center_x - width / 2, center_y - height / 2)
        _dbg('relayout: tex=%sx%s view=%sx%s -> img %sx%s pos=%s op=%.2f' % (
            texture.width, texture.height, round(self.width), round(self.height),
            round(width), round(height),
            tuple(round(v) for v in self.image.pos), self.image.opacity))

    def reset_zoom(self) -> None:
        self.zoom = 1.0
        self.offset = [0.0, 0.0]
        self._relayout()

    # -- 手势 ----------------------------------------------------------
    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        self._touch = {'start': (touch.x, touch.y), 'moved': False,
                       'double': touch.is_double_tap}
        if touch.is_double_tap:
            if self.zoom > 1.01:
                self.reset_zoom()
            else:                                  # 放大到 2 倍，以触点为中心
                self.zoom = 2.0
                self.offset = [(self.width / 2 - touch.x + self.x) * 1.0,
                               (self.height / 2 - touch.y + self.y) * 1.0]
            self._relayout()
            self._touch = None
            return True
        return True

    def on_touch_move(self, touch):
        if self._touch is None:
            return super().on_touch_move(touch)
        dx = touch.x - self._touch['start'][0]
        dy = touch.y - self._touch['start'][1]
        if abs(dx) > dp(8) or abs(dy) > dp(8):
            self._touch['moved'] = True
        if self.zoom > 1.01 and self._touch['moved']:
            self.offset[0] += touch.dx
            self.offset[1] += touch.dy
            self._relayout()
        return True

    def on_touch_up(self, touch):
        if self._touch is None:
            return super().on_touch_up(touch)
        start = self._touch['start']
        moved = self._touch['moved']
        self._touch = None
        dx = touch.x - start[0]
        dy = touch.y - start[1]
        if moved and abs(dx) > dp(60) and abs(dx) > abs(dy):
            # 左右滑动翻页（跟随阅读方向）
            forward = (dx < 0) if self.screen.direction == 'rtl' else (dx > 0)
            self.screen.next_page() if forward else self.screen.prev_page()
        elif not moved:
            self.screen.tap_page(touch.x)
        return True


class ReaderScreen(Screen):
    """阅读器：单页 / 连页，进度记忆，点击与滑动翻页"""

    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.source = None
        self.title_text = ''
        self.pages: list = []
        self.index = 0
        self.mode = app.store.setting('mode', 'single')
        self.direction = app.store.setting('direction', 'rtl')
        self._page_view = None
        self._scroll = None
        self._scroll_images = {}
        # 连页模式的滚动状态：高度前缀和缓存、占位长宽比猜测、节流用的 Clock 事件
        self._scroll_spacing = dp(2)
        self._offset_cache = None
        self._guess_ratio = 1.45
        self._idle_event = None
        self._load_event = None
        self._build_ui()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = BoxLayout(orientation='vertical')
        with root.canvas.before:
            Color(*BG)
            self._bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda *_: setattr(self._bg, 'pos', root.pos),
                  size=lambda *_: setattr(self._bg, 'size', root.size))

        bar = BoxLayout(size_hint_y=None, height=dp(46), padding=(dp(6), dp(4)),
                        spacing=dp(6))
        bar.add_widget(flat_button('←', self.app.show_browse, width=dp(44)))
        self.title_label = make_label('', font_size=sp(13), shorten=True,
                                      shorten_from='right')
        bar.add_widget(self.title_label)
        self.fav_button = flat_button('☆', self.toggle_favorite, width=dp(44))
        bar.add_widget(self.fav_button)
        self.mode_button = flat_button('单页', self.toggle_mode, width=dp(58))
        bar.add_widget(self.mode_button)
        bar.add_widget(flat_button('目录', self.show_catalog, width=dp(58)))
        root.add_widget(bar)

        self.content = FloatLayout()
        root.add_widget(self.content)

        footer = BoxLayout(size_hint_y=None, height=dp(48), padding=(dp(6), dp(4)),
                           spacing=dp(8))
        footer.add_widget(flat_button('上一页', self.prev_page, width=dp(70)))
        self.progress = make_label('', font_size=sp(12), color=(0.8, 0.8, 0.8, 1))
        footer.add_widget(self.progress)
        footer.add_widget(flat_button('下一页', self.next_page, width=dp(70)))
        root.add_widget(footer)
        self.add_widget(root)

    # ------------------------------------------------------------------
    # 打开
    # ------------------------------------------------------------------
    def open_source(self, source, title='') -> None:
        # 每次打开都换一个令牌：异步解析是并发的，旧请求的回调晚到时必须丢弃，
        # 否则「A 还在解析时打开 B」会让 A 的结果把 B 顶掉（表现为打开新漫画、
        # 阅读器里却还是上一本的画面）。
        self._open_token = getattr(self, '_open_token', 0) + 1
        token = self._open_token
        self.source = source
        self.title_text = title
        self.pages = []
        self.index = 0
        self.title_label.text = title
        self.set_status('正在解析…')
        self.apply_mode()          # pages 已清空 → 立刻移除上一本漫画的图片控件与纹理
        self.app.show_reader()
        self.app.library.open_async(
            source,
            lambda pages, loaded_title: self._on_loaded(pages, loaded_title, token),
            lambda message: self._on_error(message, token))

    def _on_loaded(self, pages, title, token=None) -> None:
        if token is not None and token != getattr(self, '_open_token', token):
            _dbg('_on_loaded: 丢弃过期结果（已打开别的漫画）')
            return
        self.pages = list(pages)
        self.title_text = title or self.title_text
        self.title_label.text = self.title_text
        self.app.store.push_recent(self.source, self.title_text)
        record = self.app.store.progress_of(self.source)
        if record.get('mode') in ('single', 'scroll'):
            self.mode = record['mode']
        if record.get('total') == len(self.pages) and record.get('index'):
            self.index = min(int(record['index']), len(self.pages) - 1)
        self.fav_button.text = '★' if self.app.store.is_favorite(self.source) else '☆'
        self.apply_mode()
        toast(self.content, f'已打开《{self.title_text}》，共 {len(self.pages)} 页')

    def _on_error(self, message: str, token=None) -> None:
        if token is not None and token != getattr(self, '_open_token', token):
            return
        self.set_status('打开失败')
        toast(self.content, f'打开失败：{message}')

    # ------------------------------------------------------------------
    # 模式与渲染
    # ------------------------------------------------------------------
    def apply_mode(self) -> None:
        self.mode_button.text = '单页' if self.mode == 'single' else '连页'
        self.app.store.set_setting('mode', self.mode)
        self._cancel_scroll_events()
        set_upload_busy(False)
        self.content.clear_widgets()
        self._page_view = None
        self._scroll = None
        self._scroll_images = {}
        self._offset_cache = None
        if not self.pages:
            return
        if self.mode == 'single':
            self._page_view = PageView(self, size_hint=(1, 1))
            self.content.add_widget(self._page_view)
            self.show_index()
        else:
            self._build_scroll_view()

    def _cancel_scroll_events(self) -> None:
        for attr in ('_idle_event', '_load_event'):
            event = getattr(self, attr, None)
            if event is not None:
                event.cancel()
                setattr(self, attr, None)

    def _build_scroll_view(self) -> None:
        # 连页阅读的性能全在这几行：
        # 1) 滚动事件（每帧都可能来）只做 O(log n) 的偏移换算，加载/解码另开节流；
        # 2) 滚动中不建纹理（见 mobui.set_upload_busy），手指停下才逐张放行；
        # 3) 占位高度按上一页的实际长宽比给，避免图片到手时整条条带猛地跳一下。
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3),
                            scroll_distance=dp(12), scroll_timeout=dp(150))
        box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=self._scroll_spacing)
        box.bind(minimum_height=box.setter('height'))
        guess = (self.width or Window.width) * self._guess_ratio
        for order, _page in enumerate(self.pages):
            image = AsyncTextureImage(size_hint_y=None, height=max(dp(80), guess),
                                      allow_stretch=True, keep_ratio=True)
            image.bind(texture=self._on_scroll_texture)
            box.add_widget(image)
            self._scroll_images[order] = image
        scroll.add_widget(box)
        self.content.add_widget(scroll)
        self._scroll = scroll
        self._offset_cache = None
        self._idle_event = None
        self._load_event = None
        self._anchor = None
        scroll.bind(scroll_y=self._on_scroll_y)
        Clock.schedule_once(lambda _dt: self._scroll_to_index(self.index), 0)

    # -- 位置换算（滚动中每帧都会调，必须便宜）--------------------------
    def _offsets(self):
        """(每页顶部偏移列表, 总高度)；只在有页面高度变化后才重算"""
        images = self._scroll_images
        count = len(images)
        cached = self._offset_cache
        if cached is not None and cached[2] == count:
            return cached[0], cached[1]
        spacing = self._scroll_spacing
        offsets = []
        cursor = 0.0
        for order in range(count):
            offsets.append(cursor)
            cursor += images[order].height + spacing
        total = max(0.0, cursor - spacing) if count else 0.0
        self._offset_cache = (offsets, total, count)
        return offsets, total

    def _current_offset(self, offsets, total) -> float:
        """视口顶部在整条条带里的位置（内容坐标，从顶部算）"""
        return (1.0 - self._scroll.scroll_y) * total

    def _on_scroll_y(self, *_args) -> None:
        """滚动事件：只更新页码，加载和解码都推迟"""
        self._idle_event and self._idle_event.cancel()
        self._idle_event = Clock.schedule_once(self._on_scroll_idle, 0.15)
        mark_scrolling()
        offsets, total = self._offsets()
        if total > 0 and offsets:
            order = bisect.bisect_right(offsets, self._current_offset(offsets, total))
            self.index = max(0, min(order - 1, len(offsets) - 1))
            self._update_progress()
        if self._load_event is None:
            self._load_event = Clock.schedule_once(self._load_visible_scroll, 0.08)

    def _on_scroll_idle(self, _dt) -> None:
        """滚动停了：放行排队中的解码，再补一次可见页加载"""
        self._idle_event = None
        set_upload_busy(False)
        self._load_visible_scroll(0)

    def _on_scroll_texture(self, image, texture) -> None:
        """纹理到手后按比例修正高度，并把视口钉在原处（否则图片一到位整条就跳）"""
        if texture is None or not self.width:
            return
        ratio = texture.height / float(texture.width or 1)
        self._guess_ratio = min(3.0, max(0.4, ratio))
        height = max(dp(80), self.width * ratio)
        if abs(height - image.height) < 1:
            return
        anchored = self._anchor_now()
        image.height = height
        self._offset_cache = None
        if anchored is not None:
            Clock.schedule_once(lambda _dt: self._restore_anchor(*anchored), 0)

    def _anchor_now(self):
        if self._scroll is None:
            return None
        offsets, total = self._offsets()
        if total <= 0 or not offsets:
            return None
        offset = self._current_offset(offsets, total)
        order = max(0, min(bisect.bisect_right(offsets, offset) - 1, len(offsets) - 1))
        return order, offset - offsets[order]

    def _restore_anchor(self, order, delta) -> None:
        if self._scroll is None:
            return
        offsets, total = self._offsets()
        if total <= 0 or order >= len(offsets):
            return
        want = offsets[order] + delta
        self._scroll.scroll_y = max(0.0, min(1.0, 1.0 - want / total))

    def _load_visible_scroll(self, _dt=0) -> None:
        """只加载视口附近的页面（当前页优先），远处的纹理释放掉"""
        self._load_event = None
        if self.mode != 'scroll' or not self._scroll_images or self._scroll is None:
            return
        offsets, total = self._offsets()
        if total <= 0 or not offsets:
            return
        view = self.content.height or Window.height
        offset = self._current_offset(offsets, total)
        top = offset - view * 0.7
        bottom = offset + view * 1.7
        first = max(0, bisect.bisect_right(offsets, top) - 1)
        last = first
        while last + 1 < len(offsets) and offsets[last + 1] <= bottom:
            last += 1
        for order in range(first, last + 1):
            image = self._scroll_images[order]
            if image.texture is None and not image.gave_up:
                self._start_scroll_image(order, image)
        # 远到看不见的整页纹理（一张 ≈ 9.8MB）释放掉，缓存里还留着，滚回来能秒贴
        keep_first = max(0, bisect.bisect_right(offsets, offset - view * 1.5) - 1)
        keep_last = min(len(offsets) - 1, last + 2)
        for order, image in self._scroll_images.items():
            if (order < keep_first or order > keep_last) and image.texture is not None:
                image.texture = None
                _dbg('release texture order=%d' % order)
        self._update_progress()

    def _start_scroll_image(self, order: int, image) -> None:
        page = self.pages[order]
        width = int(self.width) or 720
        fit = (width, 10 ** 6, 'width', 1.0)
        image.key = (str(page), fit)
        image.max_side = 2600
        image.loader = PageView._bytes_loader(page, fit)
        image.start()

    # ------------------------------------------------------------------
    # 翻页 / 进度
    # ------------------------------------------------------------------
    def tap_page(self, x: float) -> None:
        width = self.content.width or Window.width
        if self.direction == 'rtl':
            go_prev = x > width * 0.72
        else:
            go_prev = x < width * 0.28
        self.prev_page() if go_prev else self.next_page()

    def next_page(self) -> None:
        self.goto(self.index + 1)

    def prev_page(self) -> None:
        self.goto(self.index - 1)

    def goto(self, index: int) -> None:
        if not self.pages:
            return
        index = max(0, min(int(index), len(self.pages) - 1))
        changed = index != self.index
        self.index = index
        if self.mode == 'single':
            self.show_index()
        elif changed:
            self._scroll_to_index(index)
        self._update_progress()
        self.app.store.save_progress(self.source, self.index, len(self.pages),
                                     self.mode)

    def show_index(self) -> None:
        """单页模式：显示当前页并预取相邻页"""
        if self._page_view is None or not self.pages:
            return
        self._page_view.show(self.pages[self.index])
        for offset in (1, -1):
            near = self.index + offset
            if 0 <= near < len(self.pages):
                self._prefetch(self.pages[near])
        self._update_progress()

    def _prefetch(self, page) -> None:
        from mobui import (POOL, build_texture, cached_texture, prepare_image,
                           store_texture)
        width = int(self.width) or 720
        fit = (width, int(self.height) or 1280,
               self.app.store.setting('fit', 'contain'), 1.0)
        key = (str(page), fit)
        if cached_texture(key) is not None:
            return

        def worker():
            # 后台：只做取字节 + 纯 CPU 解码（CoreImage/SDL 必须在主线程调用）
            try:
                prepared = prepare_image(PageView._bytes_loader(page, fit)(), 2200)
            except Exception:
                prepared = None

            def apply(_dt):
                if prepared is None:
                    return
                try:
                    core = build_texture(prepared)
                except Exception:
                    core = None
                if core is not None:
                    store_texture(key, core)

            Clock.schedule_once(apply, 0)
        POOL.submit(worker)

    def _scroll_to_index(self, index: int) -> None:
        offsets, total = self._offsets()          # 含 spacing，和滚动事件里用的是同一套换算
        if total <= 0 or not offsets:
            return
        index = max(0, min(int(index), len(offsets) - 1))
        self._scroll.scroll_y = max(0.0, min(1.0, 1.0 - offsets[index] / total))
        Clock.schedule_once(lambda _dt: self._load_visible_scroll(), 0)

    def _update_progress(self) -> None:
        total = len(self.pages)
        if not total:
            return
        percent = (self.index + 1) * 100 // total
        self.progress.text = f'{self.index + 1}/{total}　{percent}%'

    def set_status(self, text: str) -> None:
        self.progress.text = text

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def toggle_mode(self) -> None:
        self.mode = 'scroll' if self.mode == 'single' else 'single'
        self.apply_mode()
        self._update_progress()
        self.app.store.save_progress(self.source, self.index, len(self.pages),
                                     self.mode)

    def toggle_favorite(self) -> None:
        if not self.pages:
            return
        from browse import GalleryCard
        card = GalleryCard(url=str(self.source), title=self.title_text,
                           category=('在线' if is_online_url(self.source) else '本地'),
                           pages=f'{len(self.pages)} 页')
        added = self.app.store.toggle_favorite(card)
        self.fav_button.text = '★' if added else '☆'
        toast(self.content, ('已收藏：' if added else '已取消收藏：') + self.title_text)
        self.app.refresh_favorites()

    def show_catalog(self) -> None:
        """页码目录：直接跳页（不下载缩略图，手机流量友好）"""
        if not self.pages:
            return
        grid = GridLayout(cols=5, spacing=dp(4), padding=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))
        scroll = ScrollView()
        scroll.add_widget(grid)
        popup = Popup(title=f'目录（共 {len(self.pages)} 页）', content=scroll,
                      size_hint=(0.9, 0.85), title_size=sp(14))

        def jump(order):
            popup.dismiss()
            self.goto(order)

        for order in range(len(self.pages)):
            grid.add_widget(flat_button(str(order + 1), lambda o=order: jump(o),
                                        width=dp(56), height=dp(44)))
        popup.open()

    def on_size(self, *_args) -> None:
        # 尺寸变化只需要重新摆位，绝不能重新加载整页：
        # 重新加载会让 AsyncTextureImage._token 递增、把在途解码结果作废（→ 白页死循环）
        if self.mode == 'single' and self._page_view is not None:
            Clock.schedule_once(lambda dt: self._page_view._relayout(), 0)

    def _reload_current(self) -> None:
        if self.pages and self._page_view is not None:
            self._page_view.show(self.pages[self.index])


class BrowseScreen(Screen):
    """搜索 / 收藏夹 / 最近打开 / 标签收藏 / 本地目录"""

    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.view = 'search'
        self.page = None                 # 最近一次搜索结果
        self.cards: list = []
        self.rows: list = []
        self.selected = None
        self.autosearch_ready = False
        self._build_ui()
        self.autosearch_ready = True

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = BoxLayout(orientation='vertical')
        with root.canvas.before:
            Color(*BG)
            self._bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda *_: setattr(self._bg, 'pos', root.pos),
                  size=lambda *_: setattr(self._bg, 'size', root.size))

        # 顶栏只放「输入框 + 搜索」：原来 输入框+搜索+筛选+设置 挤在 360dp 一行里，
        # 输入框被压得只剩一半，按钮也小得难点
        top = BoxLayout(size_hint_y=None, height=dp(52), padding=(dp(10), dp(8)),
                        spacing=dp(8))
        paint_round(top, THEME['panel'], 0)
        self.keyword = flat_input(hint_text='关键词 / 标签（如 language:chinese）',
                                  multiline=False, size_hint_y=None, height=dp(36),
                                  font_size=sp(13))
        self.keyword.bind(on_text_validate=lambda *_: self.do_search())
        top.add_widget(self.keyword)
        top.add_widget(flat_button('搜索', self.do_search, width=dp(64),
                                   height=dp(36), accent=True))
        root.add_widget(top)

        # 筛选面板：收起时直接从布局树里摘掉（不用 height=0 / disabled 来“藏”）。
        # 原因（实测 + 读 Kivy 源码 kivy/uix/widget.py:572）：
        #   def on_touch_down(self, touch):
        #       if self.disabled and self.collide_point(*touch.pos):
        #           return True          # 被禁用的控件只要被点到就会吞掉这次触摸
        #   - height=0 只是父容器自己变矮，子控件不会被一起收起：分类 chips 那个
        #     ScrollView(do_scroll_y=False, height=dp(76)) 会被摆到 pos=(21,2499)
        #     size=(1218,266)，正好压在顶栏上，于是「搜索框/搜索/筛选/设置」全部点不动；
        #   - 改成 disabled=True 更糟：它会「吞掉」落进来的触摸（handled=True 但不触发按钮）。
        # 摘掉整棵子树才是干净的解法。
        self.root_box = root
        self.top_bar = top
        self.filters = self._build_filters()
        root.add_widget(self.filters)
        root.remove_widget(self.filters)
        self.filters_open = False
        self.filters.disabled = False

        views = BoxLayout(size_hint_y=None, height=dp(46), padding=(dp(10), dp(5)),
                          spacing=dp(6))
        paint_round(views, THEME['panel'], 0)
        self.view_buttons = {}
        self._view_paints = {}
        for key, text in (('search', '搜索结果'), ('favorites', '收藏夹'),
                          ('recent', '最近打开')):
            button = ToggleButton(text=text, group='view', font_size=sp(12),
                                  background_normal='', background_down='',
                                  background_color=(0, 0, 0, 0),
                                  color=THEME['text'],
                                  state='down' if key == 'search' else 'normal')
            button.bind(on_release=lambda btn, k=key: self.switch_view(k))
            self._view_paints[key] = paint_round(button, THEME['button'])
            views.add_widget(button)
            self.view_buttons[key] = button
        # 「标签收藏」和「收藏标签」搬进了筛选面板，「设置」搬到底栏
        views.add_widget(flat_button('筛选', self.toggle_filters, width=dp(52),
                                     quiet=True))
        views.add_widget(flat_button('本地', self.show_local, width=dp(52),
                                     quiet=True))
        root.add_widget(views)

        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self.list_box = BoxLayout(orientation='vertical', size_hint_y=None,
                                  spacing=dp(2), padding=(0, dp(2)))
        self.list_box.bind(minimum_height=self.list_box.setter('height'))
        scroll.add_widget(self.list_box)
        root.add_widget(scroll)
        self.scroll = scroll
        # 滚动时 ScrollView 会抢走触摸，行收不到 on_release，
        # 「按下即高亮」的行就会一直亮着（划过去的整列都亮）。滚动结束后统一刷一遍。
        scroll.bind(on_scroll_stop=lambda *_: self.sync_row_selection())

        # 状态文字单独一行：原来塞在底栏里被 5 个按钮挤到只剩几十 dp，文字直接被裁掉
        status_row = BoxLayout(size_hint_y=None, height=dp(26),
                               padding=(dp(10), 0))
        self.status = make_label('输入关键词后点「搜索」，或直接看「收藏夹」',
                                 font_size=sp(12), color=THEME['dim'])
        status_row.add_widget(self.status)
        root.add_widget(status_row)

        bottom = BoxLayout(size_hint_y=None, height=dp(52), padding=(dp(10), dp(6)),
                           spacing=dp(6))
        paint_round(bottom, THEME['panel'], 0)
        self.prev_button = flat_button('上一页', lambda: self.turn_page(-1),
                                       flex=True, height=dp(40))
        bottom.add_widget(self.prev_button)
        self.next_button = flat_button('下一页', lambda: self.turn_page(1),
                                       flex=True, height=dp(40))
        bottom.add_widget(self.next_button)
        self.read_button = flat_button('阅读', self.open_selected, flex=True,
                                       height=dp(40), accent=True)
        bottom.add_widget(self.read_button)
        bottom.add_widget(flat_button('设置', self.app.show_settings, width=dp(54),
                                      height=dp(40), quiet=True))
        root.add_widget(bottom)
        self.add_widget(root)
        Clock.schedule_once(self._dbg_geometry, 1.5)

    # ------------------------------------------------------------------
    # 临时调试：坐标与触摸落点
    # ------------------------------------------------------------------
    def _dbg_geometry(self, _dt=None) -> None:
        from kivy.core.window import Window
        from kivy.metrics import Metrics
        _dbg('Window.size=%s system_size=%s dpi=%s density=%s' % (
            tuple(Window.size), tuple(Window.system_size), Window.dpi, Metrics.density))
        pairs = (('screen', self), ('top', self.keyword.parent),
                 ('keyword', self.keyword),
                 ('views', self.view_buttons['search'].parent),
                 ('list', self.list_box), ('bottom', self.status.parent))
        for name, widget in pairs:
            try:
                origin = widget.to_window(0, 0)
                rect = 'window pos=%s size=%s' % (
                    tuple(round(v) for v in origin),
                    tuple(round(v) for v in widget.size))
            except Exception as exc:            # noqa: BLE001
                rect = 'err %s' % exc
            _dbg('%-8s pos=%s size=%s | %s' % (
                name, tuple(round(v) for v in widget.pos),
                tuple(round(v) for v in widget.size), rect))
        self.keyword.bind(focus=lambda widget, value: _dbg('keyword focus ->', value))
        _dbg('softinput_mode=%s' % getattr(Window, 'softinput_mode', 'n/a'))
        try:
            _dbg('Window.children=%s' % [
                (type(c).__name__, tuple(round(v) for v in c.pos),
                 tuple(round(v) for v in c.size), c.disabled)
                for c in Window.children])
            _dbg('sm.screens=%s' % [
                (s.name, 'child' if s.parent else 'orphan', s.disabled,
                 round(s.opacity, 2), tuple(round(v) for v in s.pos),
                 tuple(round(v) for v in s.size))
                for s in self.app.sm.screens])
        except Exception as exc:                # noqa: BLE001
            _dbg('dump err', exc)

    def on_touch_down(self, touch):
        if DEBUG_UI:
            try:
                near = [name for name, widget in
                        (('keyword', self.keyword), ('top', self.keyword.parent),
                         ('views', self.view_buttons['search'].parent),
                         ('bottom', self.status.parent))
                        if widget.collide_point(*touch.pos)]
            except Exception:                   # noqa: BLE001
                near = ['err']
            _dbg('touch down pos=%s in_screen=%s hits=%s' % (
                tuple(round(v) for v in touch.pos),
                self.collide_point(*touch.pos), near))
        handled = super().on_touch_down(touch)
        if DEBUG_UI:
            _dbg('  -> handled=%s grab=%s' % (handled, touch.grab_current))
            for line in self._dbg_hit_walk(touch.pos):
                _dbg('  covers:', line)
        return handled

    def _dbg_hit_walk(self, pos) -> list:
        """列出所有压在这个点上的控件（按 window 坐标做命中测试）"""
        from kivy.core.window import Window
        found = []

        def walk(widget, path, depth):
            if depth > 7:
                return
            for child in widget.children:
                name = '%s/%s' % (path, type(child).__name__)
                try:
                    if child.collide_point(*pos):
                        found.append('%s pos=%s size=%s disabled=%s op=%.2f' % (
                            name, tuple(round(v) for v in child.pos),
                            tuple(round(v) for v in child.size),
                            child.disabled, child.opacity))
                except Exception:               # noqa: BLE001
                    pass
                walk(child, name, depth + 1)

        for child in Window.children:
            walk(child, 'Win:%s' % type(child).__name__, 0)
        return found[:12]

    def _build_filters(self) -> BoxLayout:
        panel = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(230),
                          padding=(dp(10), dp(8)), spacing=dp(8))
        paint_round(panel, THEME['panel'], 0)

        line1 = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        self.tags_input = flat_input(hint_text='包含标签（空格分隔）',
                                     multiline=False, font_size=sp(12))
        self.exclude_input = flat_input(hint_text='排除标签', multiline=False,
                                        font_size=sp(12))
        line1.add_widget(self.tags_input)
        line1.add_widget(self.exclude_input)
        panel.add_widget(line1)

        # 原来这一行还塞了「全选/重置分类」两个 76dp 按钮，
        # 38+84+38+84+76+76=396dp 已经超过 360dp 屏宽，会溢出去
        line2 = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        line2.add_widget(make_label('语言:', font_size=sp(12), size_hint_x=None,
                                    width=dp(38), color=THEME['dim']))
        self.language = Spinner(text='不限', values=[label for _k, label in LANGUAGES],
                                size_hint_x=None, width=dp(96), font_size=sp(12),
                                background_normal='', background_down='',
                                background_color=(0, 0, 0, 0), color=THEME['text'])
        paint_round(self.language, THEME['button'])
        self.language.bind(text=self._on_filter_choice)
        line2.add_widget(self.language)
        line2.add_widget(make_label('来源:', font_size=sp(12), size_hint_x=None,
                                    width=dp(38), color=THEME['dim']))
        self.source = Spinner(text=SOURCE_LABELS['search'],
                              values=[SOURCE_LABELS[key] for key in
                                      ('search', 'front', 'popular')],
                              size_hint_x=None, width=dp(96), font_size=sp(12),
                              background_normal='', background_down='',
                              background_color=(0, 0, 0, 0), color=THEME['text'])
        paint_round(self.source, THEME['button'])
        self.source.bind(text=self._on_filter_choice)
        line2.add_widget(self.source)
        panel.add_widget(line2)

        chips = ScrollView(do_scroll_y=False, size_hint_y=None, height=dp(72))
        self.cat_box = BoxLayout(size_hint_x=None, spacing=dp(6), padding=(0, dp(4)))
        self.cat_box.bind(minimum_width=self.cat_box.setter('width'))
        self.cat_vars = {}
        for key, label, bit in CATEGORIES:
            button = ToggleButton(text=label, size_hint=(None, 1), width=dp(76),
                                  font_size=sp(12), background_normal='',
                                  background_down='', background_color=(0, 0, 0, 0),
                                  color=THEME['text'])
            fill, _rect = paint_round(button, THEME['button'])
            button.bind(state=lambda widget, value, f=fill: setattr(
                f, 'rgba', THEME['accent'] if value == 'down' else THEME['button']))
            button.state = 'down' if (self.app.default_mask & bit) else 'normal'
            self.cat_vars[key] = (button, bit)
            self.cat_box.add_widget(button)
        chips.add_widget(self.cat_box)
        panel.add_widget(chips)

        # 分类快捷键 + 标签收藏：原来占着底栏，把状态文字挤没了
        line3 = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        line3.add_widget(flat_button('全选分类', lambda: self.set_all_cats(True),
                                     flex=True, quiet=True))
        line3.add_widget(flat_button('重置分类', lambda: self.set_all_cats(None),
                                     flex=True, quiet=True))
        line3.add_widget(flat_button('★当前标签', self.favorite_selected_tags,
                                     flex=True, quiet=True))
        line3.add_widget(flat_button('已收藏标签', self.show_tags, flex=True,
                                     quiet=True))
        panel.add_widget(line3)
        return panel

    def toggle_filters(self) -> None:
        """展开/收起筛选面板：摘掉整棵子树，避免它溢出后盖住顶栏抢触摸"""
        if self.filters_open:
            self.root_box.remove_widget(self.filters)
            self.filters_open = False
        else:
            # 插到顶栏下面：BoxLayout 按 reversed(children) 依次布局，
            # 所以下标取「顶栏在 children 里的位置」，面板就会排在顶栏之后、views 之前。
            idx = self.root_box.children.index(self.top_bar)
            self.root_box.add_widget(self.filters, index=idx)
            self.filters_open = True
        _dbg('toggle_filters ->', 'open' if self.filters_open else 'closed')

    def _on_filter_choice(self, *_args) -> None:
        if self.autosearch_ready and self.view == 'search':
            self.do_search()

    def set_all_cats(self, value) -> None:
        for button, bit in self.cat_vars.values():
            if value is None:
                button.state = 'down' if (self.app.default_mask & bit) else 'normal'
            else:
                button.state = 'down' if value else 'normal'
        self._on_filter_choice()

    def selected_mask(self) -> int:
        mask = 0
        for button, bit in self.cat_vars.values():
            if button.state == 'down':
                mask |= bit
        return mask

    # ------------------------------------------------------------------
    # 搜索 / 视图
    # ------------------------------------------------------------------
    def _language_key(self) -> str:
        for key, label in LANGUAGES:
            if label == self.language.text:
                return key
        return ''

    def _source_key(self) -> str:
        for key, label in SOURCE_LABELS.items():
            if label == self.source.text:
                return key
        return 'search'

    def do_search(self) -> None:
        _dbg('do_search keyword=%r tags=%r' % (self.keyword.text, self.tags_input.text))
        url = self.app.library.search_url(
            keyword=self.keyword.text, tags=self.tags_input.text,
            exclude=self.exclude_input.text, language=self._language_key(),
            source=self._source_key(), selected_mask=self.selected_mask())
        self.view = 'search'
        self.view_buttons['search'].state = 'down'
        self.status.text = '正在搜索…'
        self.app.library.search_async(url, self._on_search_done, self._on_search_fail)

    def _on_search_done(self, page) -> None:
        self.page = page
        if page.cards:
            text = (f'第 {page.page_index + 1} 页 · {len(page.cards)} 项'
                    f'　{page.total_text}')
        else:
            text = page.empty_reason or '没有结果'
        self.render_cards(page.cards, text)
        self.prev_button.disabled = not bool(page.prev_url)
        self.next_button.disabled = not bool(page.next_url)

    def _on_search_fail(self, message: str) -> None:
        self.status.text = f'搜索失败：{message}'
        toast(self.list_box, f'搜索失败：{message}')
        self.prev_button.disabled = self.next_button.disabled = True

    def turn_page(self, delta: int) -> None:
        if not self.page:
            return
        url = self.page.next_url if delta > 0 else self.page.prev_url
        if not url:
            toast(self.list_box, '没有更多了')
            return
        self.status.text = '正在翻页…'
        self.app.library.search_async(url, self._on_search_done, self._on_search_fail)

    def switch_view(self, key: str) -> None:
        _dbg('switch_view', key)
        self.view = key
        for name, button in self.view_buttons.items():
            button.state = 'down' if name == key else 'normal'
        for name, (fill, _rect) in getattr(self, '_view_paints', {}).items():
            fill.rgba = THEME['accent'] if name == key else THEME['button']
        if key == 'favorites':
            self.show_favorites()
        elif key == 'recent':
            self.show_recent()
        elif self.page is not None:
            self.render_cards(self.page.cards,
                              f'第 {self.page.page_index + 1} 页 · '
                              f'{len(self.page.cards)} 项　{self.page.total_text}')
        else:
            self.render_cards([], '点「搜索」开始，或打开「收藏夹」')
        self.prev_button.disabled = self.next_button.disabled = (key != 'search')

    def show_favorites(self) -> None:
        cards = self.app.store.favorite_cards()
        self.render_cards(cards, f'收藏夹 · {len(cards)} 部（在线与本地漫画都在这里）'
                          if cards else '收藏夹是空的：在列表里点「☆收藏」或阅读时点 ☆')

    def show_recent(self) -> None:
        from browse import GalleryCard
        cards = []
        for item in self.app.store.recent():
            url = str(item.get('url', ''))
            cards.append(GalleryCard(url=url, title=str(item.get('title') or url),
                                     category=('在线' if is_online_url(url) else '本地'),
                                     posted=str(item.get('updated', ''))))
        self.render_cards(cards, f'最近打开 · {len(cards)} 部' if cards else '还没有阅读记录')

    # ------------------------------------------------------------------
    # 列表渲染与操作
    # ------------------------------------------------------------------
    def render_cards(self, cards, status_text: str) -> None:
        self.cards = list(cards)
        self.selected = None
        self.rows = []
        self.list_box.clear_widgets()
        for card in self.cards:
            row = GalleryRow(self.app, card, on_select=self.select_row,
                             on_open=lambda widget: self.open_card(widget.card))
            self.list_box.add_widget(row)
            self.rows.append(row)
            row.load_cover()
        self.status.text = status_text

    def select_row(self, row) -> None:
        for other in self.rows:
            if other is not row:
                other.set_selected(False)
        self.selected = row

    def sync_row_selection(self) -> None:
        """把所有行的高亮对齐到当前选中行（清掉滚动时残留的高亮）"""
        for row in self.rows:
            row.set_selected(row is self.selected)

    def open_selected(self) -> None:
        if self.selected is None:
            toast(self.list_box, '先点一行选中，再点「阅读」（或双击那一行）')
            return
        self.open_card(self.selected.card)

    def open_card(self, card) -> None:
        self.app.reader.open_source(card.url, card.title or card.url)

    def toggle_selected_favorite(self) -> None:
        if self.selected is None:
            toast(self.list_box, '先选中一行')
            return
        added = self.app.store.toggle_favorite(self.selected.card)
        self.selected.refresh_star()
        toast(self.list_box, ('已收藏：' if added else '已取消收藏：') +
              (self.selected.card.title or self.selected.card.url))
        if self.view == 'favorites':
            self.show_favorites()

    def favorite_selected_tags(self) -> None:
        if self.selected is None or not self.selected.card.tags:
            toast(self.list_box, '先选中一部带标签的漫画')
            return
        added = self.app.store.add_tags(self.selected.card.tags)
        toast(self.list_box,
              f'已收藏 {added} 个标签（共 {len(self.app.store.tags())} 个）')

    def refresh_rows(self) -> None:
        """收藏状态变化后刷新 ★ 标记"""
        for row in self.rows:
            row.refresh_star()

    # ------------------------------------------------------------------
    # 标签收藏 / 本地目录
    # ------------------------------------------------------------------
    def show_tags(self) -> None:
        tags = self.app.store.tags()
        box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6),
                        padding=(dp(4), dp(4)))
        box.bind(minimum_height=box.setter('height'))
        scroll = ScrollView()
        popup = Popup(title=f'标签收藏（{len(tags)} 个）', content=scroll,
                      size_hint=(0.92, 0.8), title_size=sp(15),
                      title_color=THEME['text'], separator_color=THEME['accent'],
                      background='', background_color=(0.10, 0.11, 0.135, 0.98))
        if not tags:
            box.add_widget(make_label('还没有收藏标签：\n在筛选面板里点「★当前标签」即可收藏',
                                      font_size=sp(12), size_hint_y=None, height=dp(90),
                                      color=THEME['dim']))
        for tag in tags:
            # 原来一行是 200+80+56+间距=344dp，而弹窗内宽只有 ~320dp，
            # 「删除」被挤出屏幕；改成标签名自适应 + 两个小按钮
            row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
            row.add_widget(flat_button(tag, lambda t=tag: (popup.dismiss(),
                                                           self.use_tag(t)),
                                       flex=True, height=dp(38)))
            row.add_widget(flat_button('加入搜索', lambda t=tag: (popup.dismiss(),
                                                               self.use_tag(t)),
                                       width=dp(76), height=dp(38), quiet=True))
            def remove(t=tag, p=popup):
                self.app.store.remove_tag(t)
                p.dismiss()
                self.show_tags()
            row.add_widget(flat_button('删除', remove, width=dp(56), height=dp(38),
                                       quiet=True))
            box.add_widget(row)
        scroll.add_widget(box)
        popup.open()

    def use_tag(self, tag: str) -> None:
        current = self.tags_input.text.strip()
        if tag not in split_terms(current):
            self.tags_input.text = (current + ' ' + tag).strip()
        self.do_search()

    def show_local(self) -> None:
        """本地目录扫描放到后台线程：原来在主线程同步扫 /sdcard，实测卡 5 秒以上"""
        import threading
        _dbg('show_local: popup open, scanning in background')
        box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
        box.bind(minimum_height=box.setter('height'))
        scroll = ScrollView()
        popup = Popup(title='本地漫画（扫描中…）', content=scroll,
                      size_hint=(0.92, 0.8), title_size=sp(14))
        hint = make_label('正在扫描本地目录…', font_size=sp(12), size_hint_y=None,
                          height=dp(60), color=(0.85, 0.85, 0.85, 1))
        box.add_widget(hint)
        scroll.add_widget(box)
        popup.open()

        def worker() -> None:
            choices = self.app.library.local_choices()

            def apply(_dt=None) -> None:
                _dbg('show_local: found', len(choices))
                box.remove_widget(hint)
                popup.title = f'本地漫画（{len(choices)} 个）'
                if not choices:
                    box.add_widget(make_label(
                        '没有找到本地漫画。\n可在「设置」里填写目录（安卓示例：/sdcard/Download），\n'
                        '并授予文件访问权限。',
                        font_size=sp(12), size_hint_y=None, height=dp(120),
                        color=(0.85, 0.85, 0.85, 1)))
                for path in choices:
                    box.add_widget(flat_button(
                        str(path),
                        lambda p=path: (popup.dismiss(),
                                        self.app.reader.open_source(str(p), str(p))),
                        width=dp(300), height=dp(44)))

            Clock.schedule_once(apply, 0)

        threading.Thread(target=worker, daemon=True, name='eh-local-scan').start()


class SettingsScreen(Screen):
    """代理 / Cookie / 本地目录 / 阅读方向 / 数据管理"""

    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self._build_ui()

    def _build_ui(self) -> None:
        root = BoxLayout(orientation='vertical')
        with root.canvas.before:
            Color(*BG)
            self._bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda *_: setattr(self._bg, 'pos', root.pos),
                  size=lambda *_: setattr(self._bg, 'size', root.size))

        bar = BoxLayout(size_hint_y=None, height=dp(46), padding=(dp(6), dp(4)),
                        spacing=dp(6))
        bar.add_widget(flat_button('←', self.app.show_browse, width=dp(44)))
        bar.add_widget(make_label('设置', font_size=sp(14)))
        bar.add_widget(flat_button('保存', self.save, width=dp(60)))
        root.add_widget(bar)

        scroll = ScrollView()
        box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6),
                        padding=dp(10))
        box.bind(minimum_height=box.setter('height'))

        box.add_widget(make_label('代理（留空=跟随系统代理；direct=直连；也可填 '
                                  'http://127.0.0.1:7890 或 socks5://…）',
                                  font_size=sp(11), color=(0.8, 0.8, 0.8, 1),
                                  size_hint_y=None, height=dp(44)))
        self.proxy_input = flat_input(text=self.app.store.proxy(), multiline=False,
                                      size_hint_y=None, height=dp(40), font_size=sp(12))
        box.add_widget(self.proxy_input)
        self.proxy_label = make_label('', font_size=sp(11), size_hint_y=None,
                                      height=dp(26), color=(0.62, 0.82, 1, 1))
        box.add_widget(self.proxy_label)

        box.add_widget(make_label('Cookie（需要登录 / 提示 509 时填写）',
                                  font_size=sp(12), size_hint_y=None, height=dp(30)))
        self.cookie_input = flat_input(text=self.app.store.cookie(), multiline=True,
                                       size_hint_y=None, height=dp(110),
                                       font_size=sp(12))
        box.add_widget(self.cookie_input)
        box.add_widget(flat_button('测试连接', self.test_connection, width=dp(110)))
        self.test_label = make_label('', font_size=sp(11), size_hint_y=None,
                                     height=dp(40), color=(0.62, 1, 0.7, 1))
        box.add_widget(self.test_label)

        box.add_widget(make_label('本地漫画目录（每行一个；安卓示例 /sdcard/Download）',
                                  font_size=sp(12), size_hint_y=None, height=dp(40)))
        self.local_input = flat_input(
            text='\n'.join(str(item) for item in
                           (self.app.store.setting('local_dirs') or [])),
            multiline=True, size_hint_y=None, height=dp(110), font_size=sp(12))
        box.add_widget(self.local_input)

        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        row.add_widget(make_label('阅读方向:', font_size=sp(12), size_hint_x=None,
                                  width=dp(80)))
        self.direction_spinner = Spinner(
            text=('从右到左' if self.app.store.setting('direction') == 'rtl'
                  else '从左到右'),
            values=['从右到左', '从左到右'], size_hint_x=None, width=dp(120))
        row.add_widget(self.direction_spinner)
        row.add_widget(make_label('缩放:', font_size=sp(12), size_hint_x=None,
                                  width=dp(50)))
        self.fit_spinner = Spinner(
            text=('适应窗口' if self.app.store.setting('fit') == 'contain'
                  else '适应宽度'),
            values=['适应窗口', '适应宽度'], size_hint_x=None, width=dp(110))
        row.add_widget(self.fit_spinner)
        box.add_widget(row)

        box.add_widget(make_label(f'数据目录：{data_dir()}', font_size=sp(10),
                                  size_hint_y=None, height=dp(40),
                                  color=(0.6, 0.6, 0.6, 1)))
        manage = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        manage.add_widget(flat_button('清空收藏', self.clear_favorites, width=dp(90)))
        manage.add_widget(flat_button('清空标签', self.clear_tags, width=dp(90)))
        manage.add_widget(flat_button('清空阅读记录', self.clear_progress, width=dp(110)))
        box.add_widget(manage)

        scroll.add_widget(box)
        root.add_widget(scroll)
        self.add_widget(root)

    def on_pre_enter(self, *_args) -> None:
        self.proxy_input.text = self.app.store.proxy()
        self.cookie_input.text = self.app.store.cookie()
        self.proxy_label.text = f'当前生效：{self.app.store.proxy_text()}'

    def save(self) -> None:
        self.app.store.set_setting('proxy', self.proxy_input.text.strip())
        self.app.store.set_setting('cookie', self.cookie_input.text.strip())
        dirs = [line.strip() for line in self.local_input.text.splitlines()
                if line.strip()]
        self.app.store.set_setting('local_dirs', dirs)
        self.app.store.set_setting(
            'direction', 'rtl' if self.direction_spinner.text == '从右到左' else 'ltr')
        self.app.store.set_setting(
            'fit', 'contain' if self.fit_spinner.text == '适应窗口' else 'width')
        self.proxy_label.text = f'当前生效：{self.app.store.proxy_text()}'
        self.app.reader.direction = self.app.store.setting('direction', 'rtl')
        toast(self, '设置已保存')

    def test_connection(self) -> None:
        """后台请求站点，结果回主线程显示"""
        import threading

        from pages import HttpFetcher
        self.test_label.text = '正在测试…'
        proxy = self.proxy_input.text.strip()
        cookie = self.cookie_input.text.strip()

        def worker():
            try:
                fetcher = HttpFetcher(cookie=cookie, proxy=proxy, retries=0, timeout=20)
                page_html = fetcher.text('https://e-hentai.org/')
                if 'Just a moment' in page_html:
                    message = f'失败：触发了人机验证（{fetcher.describe()}）'
                elif '<title>' in page_html:
                    message = f'成功：e-hentai.org 可访问（{fetcher.describe()}）'
                else:
                    message = f'失败：返回内容异常（{fetcher.describe()}）'
            except Exception as exc:
                message = f'失败：{exc}'
            Clock.schedule_once(lambda dt: setattr(self.test_label, 'text', message), 0)

        threading.Thread(target=worker, daemon=True, name='conn-test').start()

    def clear_favorites(self) -> None:
        self.app.store.data['favorites'] = []
        self.app.store.save()
        self.app.refresh_favorites()
        toast(self, '收藏已清空')

    def clear_tags(self) -> None:
        self.app.store.clear_tags()
        toast(self, '标签收藏已清空')

    def clear_progress(self) -> None:
        self.app.store.data['progress'] = {}
        self.app.store.data['recent'] = []
        self.app.store.save()
        toast(self, '阅读记录已清空')


class MobileApp(App):
    """应用入口：安卓上由 buildozer 打成 APK，桌面上直接 python main.py 预览"""

    title = '漫画阅读器'
    store_path = None                 # 测试/调试时可指定存储文件

    def build(self):
        Window.clearcolor = BG
        self.store = Store(self.store_path)
        self.library = Library(
            self.store,
            dispatcher=lambda func: Clock.schedule_once(lambda _dt: func(), 0))
        self.default_mask = DEFAULT_SELECTED_MASK

        manager = ScreenManager(transition=SlideTransition(duration=0.15))
        self.browse = BrowseScreen(self, name='browse')
        self.reader = ReaderScreen(self, name='reader')
        self.settings = SettingsScreen(self, name='settings')
        manager.add_widget(self.browse)
        manager.add_widget(self.reader)
        manager.add_widget(self.settings)
        self.sm = manager
        Window.bind(on_keyboard=self.on_keyboard)
        Clock.schedule_once(self.after_start, 0.4)
        return manager

    def after_start(self, _dt=None) -> None:
        """启动后自动打开收藏夹（有内容时）或提示搜索"""
        proxy = self.store.proxy_text()
        if self.store.favorites():
            self.browse.switch_view('favorites')
        else:
            toast(self.browse, f'点「搜索」找漫画；代理：{proxy}')

    # -- 页面切换 -------------------------------------------------------
    def show_browse(self) -> None:
        self.sm.transition.direction = 'right'
        self.sm.current = 'browse'

    def show_reader(self) -> None:
        self.sm.transition.direction = 'left'
        self.sm.current = 'reader'

    def show_settings(self) -> None:
        self.sm.transition.direction = 'left'
        self.sm.current = 'settings'

    def refresh_favorites(self, rows_only: bool = False) -> None:
        if getattr(self, 'browse', None) is not None:
            self.browse.refresh_rows()
            if not rows_only and self.browse.view == 'favorites':
                self.browse.show_favorites()

    # -- 安卓返回键 / ESC ------------------------------------------------
    def on_keyboard(self, _window, key, _scancode=None, _codepoint=None, _mod=None):
        if key == 27:
            if self.sm.current != 'browse':
                self.show_browse()
                return True
            return False
        return False

    def on_pause(self) -> bool:
        return True                     # 安卓切后台保持状态

    def on_resume(self) -> None:
        pass


def main() -> None:
    MobileApp().run()


if __name__ == '__main__':
    main()









