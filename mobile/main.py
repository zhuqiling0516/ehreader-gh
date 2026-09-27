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

from browse import (CATEGORIES, DEFAULT_SELECTED_MASK, LANGUAGES, SOURCE_LABELS,
                    split_terms)
from ehapi import Library, Store, data_dir
from pages import is_online_url
from mobui import AsyncTextureImage, GalleryRow, make_label, toast

BG = (0.07, 0.07, 0.07, 1)
PANEL = (0.12, 0.12, 0.12, 1)
ACCENT = (0.24, 0.55, 0.85, 1)


def flat_button(text, on_press=None, width=None, height=dp(40), **kwargs):
    """统一风格的按钮"""
    button = Button(text=text, size_hint=(None, None),
                    size=(width or dp(72), height), **kwargs)
    if on_press:
        button.bind(on_release=lambda *_: on_press())
    return button


class PageView(FloatLayout):
    """单页阅读视图：自适应缩放、双击放大、放大后拖动、点击/滑动翻页"""

    def __init__(self, screen, **kwargs):
        super().__init__(**kwargs)
        self.screen = screen
        self.zoom = 1.0
        self.offset = [0.0, 0.0]
        self.image = AsyncTextureImage(size_hint=(None, None))
        self.add_widget(self.image)
        self._touch = None
        self.bind(pos=self._relayout, size=self._relayout)
        self.image.bind(texture=lambda *_: Clock.schedule_once(self._relayout, 0))

    # -- 显示 ----------------------------------------------------------
    def show(self, page) -> None:
        fit_kind = self.screen.app.store.setting('fit', 'contain')
        fit = (int(self.width) or 720, int(self.height) or 1280,
               fit_kind if fit_kind in ('contain', 'width') else 'contain', 1.0)
        key = (str(page), fit)
        self.zoom = 1.0
        self.offset = [0.0, 0.0]
        self.image.key = key
        self.image.max_side = 2200
        self.image.loader = self._bytes_loader(page, fit)
        self.image.start()
        self._relayout()

    @staticmethod
    def _bytes_loader(page, fit):
        """pages.LocalPage / OnlinePage 都提供 fetch(fit) → PIL 图，
        这里转成 PNG 字节，统一走 Pillow 解码链路"""
        def load() -> bytes:
            raw = page.fetch(fit)          # 已经按 fit 缩放过的 PIL 图
            buffer = io.BytesIO()
            raw.save(buffer, 'PNG')
            return buffer.getvalue()
        return load

    # -- 布局 ----------------------------------------------------------
    def _relayout(self, *_args) -> None:
        texture = self.image.texture
        if texture is None or not self.width or not self.height:
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
        self.source = source
        self.title_text = title
        self.pages = []
        self.index = 0
        self.title_label.text = title
        self.set_status('正在解析…')
        self.app.show_reader()
        self.app.library.open_async(source, self._on_loaded, self._on_error)

    def _on_loaded(self, pages, title) -> None:
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

    def _on_error(self, message: str) -> None:
        self.set_status('打开失败')
        toast(self.content, f'打开失败：{message}')

    # ------------------------------------------------------------------
    # 模式与渲染
    # ------------------------------------------------------------------
    def apply_mode(self) -> None:
        self.mode_button.text = '单页' if self.mode == 'single' else '连页'
        self.app.store.set_setting('mode', self.mode)
        self.content.clear_widgets()
        self._page_view = None
        self._scroll = None
        self._scroll_images = {}
        if not self.pages:
            return
        if self.mode == 'single':
            self._page_view = PageView(self, size_hint=(1, 1))
            self.content.add_widget(self._page_view)
            self.show_index()
        else:
            self._build_scroll_view()

    def _build_scroll_view(self) -> None:
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
        box.bind(minimum_height=box.setter('height'))
        for order, _page in enumerate(self.pages):
            image = AsyncTextureImage(size_hint_y=None, height=dp(400),
                                      allow_stretch=True, keep_ratio=True)
            image.bind(texture=self._on_scroll_texture)
            box.add_widget(image)
            self._scroll_images[order] = image
        scroll.add_widget(box)
        self.content.add_widget(scroll)
        self._scroll = scroll
        scroll.bind(scroll_y=lambda *_: self._load_visible_scroll())
        Clock.schedule_once(lambda _dt: self._scroll_to_index(self.index), 0)

    def _on_scroll_texture(self, image, texture) -> None:
        """纹理到手后按比例修正高度"""
        if texture is None or not self.width:
            return
        ratio = texture.height / float(texture.width or 1)
        image.height = max(dp(80), self.width * ratio)

    def _load_visible_scroll(self) -> None:
        """只加载视口附近两屏的页面，其余保持占位"""
        if self.mode != 'scroll' or not self._scroll_images or self._scroll is None:
            return
        heights = [self._scroll_images[order].height
                   for order in sorted(self._scroll_images)]
        total = sum(heights) or 1
        view = self.content.height or Window.height
        offset = (1.0 - self._scroll.scroll_y) * total
        cursor = 0
        current = 0
        for order, height in enumerate(heights):
            if cursor + height >= offset:
                current = order
                break
            cursor += height
        self.index = min(current, len(self.pages) - 1)
        top, bottom = offset - view, offset + view * 2
        cursor = 0
        for order, height in enumerate(heights):
            if cursor + height >= top and cursor <= bottom:
                image = self._scroll_images[order]
                if image.texture is None:
                    self._start_scroll_image(order, image)
            cursor += height
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
        from mobui import POOL, cached_texture, decode_texture, store_texture
        width = int(self.width) or 720
        fit = (width, int(self.height) or 1280,
               self.app.store.setting('fit', 'contain'), 1.0)
        key = (str(page), fit)
        if cached_texture(key) is not None:
            return

        def worker():
            try:
                raw = page.fetch(fit)
                buffer = io.BytesIO()
                raw.save(buffer, 'PNG')
                core = decode_texture(buffer.getvalue(), 2200)
                if core is not None:
                    store_texture(key, core)
            except Exception:
                pass
        POOL.submit(worker)

    def _scroll_to_index(self, index: int) -> None:
        heights = [self._scroll_images[order].height
                   for order in sorted(self._scroll_images)]
        total = sum(heights)
        if total <= 0:
            return
        before = sum(heights[:index])
        self._scroll.scroll_y = max(0.0, min(1.0, 1.0 - before / total))
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
        if self.mode == 'single' and self._page_view is not None:
            Clock.schedule_once(lambda dt: self._reload_current(), 0)

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

        top = BoxLayout(size_hint_y=None, height=dp(46), padding=(dp(6), dp(4)),
                        spacing=dp(6))
        self.keyword = TextInput(hint_text='关键词 / 标签（如 language:chinese）',
                                 multiline=False, size_hint_y=None, height=dp(38))
        self.keyword.bind(on_text_validate=lambda *_: self.do_search())
        top.add_widget(self.keyword)
        top.add_widget(flat_button('搜索', self.do_search, width=dp(56)))
        top.add_widget(flat_button('筛选', self.toggle_filters, width=dp(56)))
        top.add_widget(flat_button('设置', self.app.show_settings, width=dp(56)))
        root.add_widget(top)

        self.filters = self._build_filters()
        self.filters.size_hint_y = None
        self.filters.height = 0
        self.filters.opacity = 0
        root.add_widget(self.filters)

        views = BoxLayout(size_hint_y=None, height=dp(42), padding=(dp(6), dp(2)),
                          spacing=dp(4))
        self.view_buttons = {}
        for key, text in (('search', '搜索结果'), ('favorites', '收藏夹'),
                          ('recent', '最近打开')):
            button = ToggleButton(text=text, group='view',
                                  state='down' if key == 'search' else 'normal')
            button.bind(on_release=lambda btn, k=key: self.switch_view(k))
            views.add_widget(button)
            self.view_buttons[key] = button
        views.add_widget(flat_button('标签收藏', self.show_tags, width=dp(76)))
        views.add_widget(flat_button('本地', self.show_local, width=dp(52)))
        root.add_widget(views)

        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self.list_box = BoxLayout(orientation='vertical', size_hint_y=None,
                                  spacing=dp(2), padding=(0, dp(2)))
        self.list_box.bind(minimum_height=self.list_box.setter('height'))
        scroll.add_widget(self.list_box)
        root.add_widget(scroll)
        self.scroll = scroll

        bottom = BoxLayout(size_hint_y=None, height=dp(48), padding=(dp(6), dp(4)),
                           spacing=dp(4))
        self.status = make_label('输入关键词后点「搜索」，或直接看「收藏夹」',
                                 font_size=sp(11), color=(0.75, 0.75, 0.75, 1))
        bottom.add_widget(self.status)
        self.prev_button = flat_button('上一页', lambda: self.turn_page(-1), width=dp(62))
        bottom.add_widget(self.prev_button)
        self.next_button = flat_button('下一页', lambda: self.turn_page(1), width=dp(62))
        bottom.add_widget(self.next_button)
        self.read_button = flat_button('阅读', self.open_selected, width=dp(52))
        bottom.add_widget(self.read_button)
        self.fav_button = flat_button('☆收藏', self.toggle_selected_favorite,
                                      width=dp(62))
        bottom.add_widget(self.fav_button)
        bottom.add_widget(flat_button('收藏标签', self.favorite_selected_tags,
                                      width=dp(74)))
        root.add_widget(bottom)
        self.add_widget(root)

    def _build_filters(self) -> BoxLayout:
        panel = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(200),
                          padding=(dp(6), dp(2)), spacing=dp(4))
        line1 = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(4))
        self.tags_input = TextInput(hint_text='包含标签（空格分隔）', multiline=False)
        self.exclude_input = TextInput(hint_text='排除标签', multiline=False)
        line1.add_widget(self.tags_input)
        line1.add_widget(self.exclude_input)
        panel.add_widget(line1)

        line2 = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(4))
        line2.add_widget(make_label('语言:', font_size=sp(12), size_hint_x=None,
                                    width=dp(40)))
        self.language = Spinner(text='不限', values=[label for _k, label in LANGUAGES],
                                size_hint_x=None, width=dp(80))
        self.language.bind(text=self._on_filter_choice)
        line2.add_widget(self.language)
        line2.add_widget(make_label('来源:', font_size=sp(12), size_hint_x=None,
                                    width=dp(40)))
        self.source = Spinner(text=SOURCE_LABELS['search'],
                              values=[SOURCE_LABELS[key] for key in
                                      ('search', 'front', 'popular')],
                              size_hint_x=None, width=dp(80))
        self.source.bind(text=self._on_filter_choice)
        line2.add_widget(self.source)
        line2.add_widget(flat_button('全选分类', lambda: self.set_all_cats(True),
                                     width=dp(76)))
        line2.add_widget(flat_button('重置分类', lambda: self.set_all_cats(None),
                                     width=dp(76)))
        panel.add_widget(line2)

        chips = ScrollView(do_scroll_y=False, size_hint_y=None, height=dp(76))
        self.cat_box = BoxLayout(size_hint_x=None, spacing=dp(4), padding=(0, dp(4)))
        self.cat_box.bind(minimum_width=self.cat_box.setter('width'))
        self.cat_vars = {}
        for key, label, bit in CATEGORIES:
            button = ToggleButton(text=label, size_hint=(None, 1), width=dp(72))
            button.state = 'down' if (self.app.default_mask & bit) else 'normal'
            self.cat_vars[key] = (button, bit)
            self.cat_box.add_widget(button)
        chips.add_widget(self.cat_box)
        panel.add_widget(chips)
        return panel

    def toggle_filters(self) -> None:
        shown = self.filters.height > 0
        self.filters.height = 0 if shown else dp(200)
        self.filters.opacity = 0 if shown else 1

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
        self.view = key
        for name, button in self.view_buttons.items():
            button.state = 'down' if name == key else 'normal'
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
        self.fav_button.text = '☆收藏'

    def select_row(self, row) -> None:
        for other in self.rows:
            if other is not row:
                other.set_selected(False)
        self.selected = row
        self.fav_button.text = ('★取消' if self.app.store.is_favorite(row.card.url)
                               else '☆收藏')

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
        self.fav_button.text = '★取消' if added else '☆收藏'
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
        if self.selected is not None:
            self.fav_button.text = ('★取消' if self.app.store.is_favorite(
                self.selected.card.url) else '☆收藏')

    # ------------------------------------------------------------------
    # 标签收藏 / 本地目录
    # ------------------------------------------------------------------
    def show_tags(self) -> None:
        tags = self.app.store.tags()
        box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
        box.bind(minimum_height=box.setter('height'))
        scroll = ScrollView()
        popup = Popup(title=f'标签收藏（{len(tags)} 个）', content=scroll,
                      size_hint=(0.92, 0.8), title_size=sp(14))
        if not tags:
            box.add_widget(make_label('还没有收藏标签：\n选中一部漫画后点「收藏标签」，'
                                      '或在筛选里填好标签再收藏',
                                      font_size=sp(12), size_hint_y=None, height=dp(80),
                                      color=(0.8, 0.8, 0.8, 1)))
        for tag in tags:
            row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
            row.add_widget(flat_button(tag, lambda t=tag: (popup.dismiss(),
                                                           self.use_tag(t)),
                                       width=dp(200), height=dp(40)))
            row.add_widget(flat_button('加入搜索', lambda t=tag: (popup.dismiss(),
                                                               self.use_tag(t)),
                                       width=dp(80), height=dp(40)))
            def remove(t=tag, p=popup):
                self.app.store.remove_tag(t)
                p.dismiss()
                self.show_tags()
            row.add_widget(flat_button('删除', remove, width=dp(56), height=dp(40)))
            box.add_widget(row)
        scroll.add_widget(box)
        popup.open()

    def use_tag(self, tag: str) -> None:
        current = self.tags_input.text.strip()
        if tag not in split_terms(current):
            self.tags_input.text = (current + ' ' + tag).strip()
        self.do_search()

    def show_local(self) -> None:
        choices = self.app.library.local_choices()
        box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
        box.bind(minimum_height=box.setter('height'))
        scroll = ScrollView()
        popup = Popup(title=f'本地漫画（{len(choices)} 个）', content=scroll,
                      size_hint=(0.92, 0.8), title_size=sp(14))
        if not choices:
            box.add_widget(make_label(
                '没有找到本地漫画。\n可在「设置」里填写目录（安卓示例：/sdcard/Download），\n'
                '并授予文件访问权限。',
                font_size=sp(12), size_hint_y=None, height=dp(120),
                color=(0.85, 0.85, 0.85, 1)))
        for path in choices:
            box.add_widget(flat_button(str(path),
                                       lambda p=path: (popup.dismiss(),
                                                       self.app.reader.open_source(
                                                           str(p), str(p))),
                                       width=dp(300), height=dp(44)))
        scroll.add_widget(box)
        popup.open()


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
        self.proxy_input = TextInput(text=self.app.store.proxy(), multiline=False,
                                     size_hint_y=None, height=dp(40))
        box.add_widget(self.proxy_input)
        self.proxy_label = make_label('', font_size=sp(11), size_hint_y=None,
                                      height=dp(26), color=(0.62, 0.82, 1, 1))
        box.add_widget(self.proxy_label)

        box.add_widget(make_label('Cookie（需要登录 / 提示 509 时填写）',
                                  font_size=sp(12), size_hint_y=None, height=dp(30)))
        self.cookie_input = TextInput(text=self.app.store.cookie(), multiline=True,
                                      size_hint_y=None, height=dp(110))
        box.add_widget(self.cookie_input)
        box.add_widget(flat_button('测试连接', self.test_connection, width=dp(110)))
        self.test_label = make_label('', font_size=sp(11), size_hint_y=None,
                                     height=dp(40), color=(0.62, 1, 0.7, 1))
        box.add_widget(self.test_label)

        box.add_widget(make_label('本地漫画目录（每行一个；安卓示例 /sdcard/Download）',
                                  font_size=sp(12), size_hint_y=None, height=dp(40)))
        self.local_input = TextInput(
            text='\n'.join(str(item) for item in
                           (self.app.store.setting('local_dirs') or [])),
            multiline=True, size_hint_y=None, height=dp(110))
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









