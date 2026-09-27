# -*- coding: utf-8 -*-
"""Kivy 通用控件：异步取图、列表行、提示条

取图流程（安卓与桌面一致）：
    后台线程 取字节 → Pillow 解码/缩放 → 转 PNG 字节
    →  主线程 CoreImage(ext='png').texture  →  贴到 Image

之所以绕一圈 Pillow：站点图片大量是 webp，Pillow 的解码支持最稳，
还能顺手把长图缩到屏幕需要的尺寸，省内存。
"""

from __future__ import annotations

import io
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

from PIL import Image as PILImage

POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix='mobile-img')
TEXTURES: "OrderedDict[tuple, object]" = OrderedDict()
TEXTURE_LIMIT = 28
_lock = threading.Lock()


def make_label(text: str = '', font_size=sp(13), color=(1, 1, 1, 1), **kwargs):
    """带自动折行/省略的 Label（Kivy 需要手动把 text_size 绑到自身尺寸）"""
    label = Label(text=text, font_size=font_size, color=color,
                  halign='left', valign='middle', **kwargs)
    label.bind(size=lambda widget, size: setattr(widget, 'text_size',
                                                 (size[0], size[1])))
    return label


def decode_texture(data: bytes, max_side: int = 1600):
    """字节流 → Kivy CoreImage（Pillow 解码，统一转 PNG）"""
    with PILImage.open(io.BytesIO(data)) as source:
        if (source.format or '').upper() in ('JPEG', 'MPO'):
            source.draft('RGB', (max_side, max_side))
        source.load()
        if source.mode in ('RGB', 'L', 'RGBA'):
            image = source.copy()
        else:
            image = source.convert('RGB')
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    buffer.seek(0)
    core = CoreImage(buffer, ext='png')
    return core if core.texture is not None else None


def store_texture(key: tuple, core) -> None:
    with _lock:
        TEXTURES[key] = core
        TEXTURES.move_to_end(key)
        while len(TEXTURES) > TEXTURE_LIMIT:
            TEXTURES.popitem(last=False)


def cached_texture(key: tuple):
    with _lock:
        core = TEXTURES.get(key)
        if core is not None:
            TEXTURES.move_to_end(key)
        return core


class AsyncTextureImage(Image):
    """按需异步加载的图片

    key    : 缓存键，例如 ('cover', url) 或 (str(page), fit)
    loader : 无参函数，返回图片字节（在后台线程里调用）
    """

    def __init__(self, key=None, loader=None, max_side=1600, **kwargs):
        kwargs.setdefault('mipmap', False)
        super().__init__(**kwargs)
        self.key = key
        self.loader = loader
        self.max_side = max_side
        self._token = 0

    def start(self) -> bool:
        """开始加载；返回 True 表示需要等待（缓存未命中）"""
        if self.key is None or self.loader is None:
            return False
        core = cached_texture(self.key)
        if core is not None:
            self.texture = core.texture
            return False
        self._token += 1
        token = self._token
        key, loader, max_side = self.key, self.loader, self.max_side

        def worker():
            try:
                core_image = decode_texture(loader(), max_side)
            except Exception:
                core_image = None
            if core_image is not None:
                store_texture(key, core_image)

            def apply(_dt):
                if token != self._token:
                    return                       # 已经翻页/换图，丢弃旧结果
                try:
                    self.texture = core_image.texture if core_image else None
                except Exception:
                    self.texture = None
            Clock.schedule_once(apply, 0)

        POOL.submit(worker)
        return True

    def cancel(self) -> None:
        self._token += 1


class GalleryRow(ButtonBehavior, BoxLayout):
    """漫画列表里的一行：封面 + 标题 + 元信息 + 标签 + ★"""

    def __init__(self, reader_app, card, on_select=None, on_open=None, **kwargs):
        super().__init__(orientation='horizontal', size_hint_y=None,
                         height=dp(98), padding=(dp(6), dp(4)),
                         spacing=dp(8), **kwargs)
        self.app = reader_app
        self.card = card
        self.on_select = on_select
        self.on_open = on_open

        with self.canvas.before:
            self._bg_color = Color(0.14, 0.14, 0.14, 1)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg,
                  on_press=self._pressed, on_release=self._released)

        cover_box = BoxLayout(size_hint=(None, 1), width=dp(74))
        with cover_box.canvas.before:
            Color(0.09, 0.09, 0.09, 1)
            self._cover_bg = Rectangle(pos=cover_box.pos, size=cover_box.size)
        cover_box.bind(pos=lambda *_: setattr(self._cover_bg, 'pos', cover_box.pos),
                       size=lambda *_: setattr(self._cover_bg, 'size', cover_box.size))
        self.cover = AsyncTextureImage(size_hint=(1, 1), allow_stretch=True,
                                       keep_ratio=True)
        cover_box.add_widget(self.cover)
        self.add_widget(cover_box)

        info = BoxLayout(orientation='vertical', spacing=dp(1))
        info.add_widget(make_label(card.title or card.url, font_size=sp(14),
                                   shorten=True, shorten_from='right', max_lines=2,
                                   size_hint_y=0.5))
        meta = ' · '.join(part for part in (
            card.category, card.pages,
            f'★{card.rating}' if card.rating else '',
            card.uploader, card.posted) if part)
        info.add_widget(make_label(meta or '—', font_size=sp(11),
                                   color=(0.62, 0.82, 1, 1), shorten=True,
                                   shorten_from='right', size_hint_y=0.26))
        info.add_widget(make_label(' · '.join(card.tags[:6]), font_size=sp(10),
                                   color=(0.68, 0.68, 0.68, 1), shorten=True,
                                   shorten_from='right', size_hint_y=0.24))
        self.add_widget(info)

        self.star = Label(text='★' if reader_app.store.is_favorite(card.url) else '',
                          font_size=sp(16), color=(0.95, 0.76, 0.25, 1),
                          size_hint=(None, 1), width=dp(20))
        self.add_widget(self.star)
        self.selected = False

    # -- 外观 ----------------------------------------------------------
    def _sync_bg(self, *_args) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size

    def set_selected(self, flag: bool) -> None:
        self.selected = flag
        self._bg_color.rgba = (0.17, 0.28, 0.45, 1) if flag else (0.14, 0.14, 0.14, 1)

    def refresh_star(self) -> None:
        self.star.text = '★' if self.app.store.is_favorite(self.card.url) else ''

    def load_cover(self) -> None:
        url = getattr(self.card, 'thumb', '')
        if not url:
            return
        fetcher = self.app.store.fetcher()
        self.cover.key = ('cover', url)
        self.cover.max_side = 260
        self.cover.loader = (lambda u=url, f=fetcher:
                             f.content(u, referer='https://e-hentai.org/'))
        self.cover.start()

    # -- 触摸 ----------------------------------------------------------
    def _pressed(self, *_args) -> None:
        self.set_selected(True)

    def _released(self, *_args) -> None:
        if self.on_select:
            self.on_select(self)

    def on_touch_down(self, touch):
        if touch.is_double_tap and self.collide_point(*touch.pos):
            if self.on_open:
                self.on_open(self)
            return True
        return super().on_touch_down(touch)


def toast(anchor, text: str, duration: float = 2.4) -> None:
    """在窗口底部弹出一条提示

    注意：Kivy 的 Window.parent 指向自己（自引用），
    所以不能用「沿 parent 向上找根」的写法，直接用 Window 当容器即可。
    """
    if not text:
        return
    label = Label(text=text, font_size=sp(12), size_hint=(None, None),
                  color=(1, 1, 1, 0.96), padding=(dp(12), dp(8)),
                  halign='center', valign='middle')
    max_width = max(dp(200), Window.width - dp(60))
    label.text_size = (max_width, None)          # 过长的提示自动换行
    label.bind(texture_size=lambda widget, size:
               setattr(widget, 'size', (size[0], size[1])))
    label.bind(size=lambda widget, size:
               setattr(widget, 'pos', ((Window.width - size[0]) / 2, dp(60))))
    with label.canvas.before:
        Color(0, 0, 0, 0.8)
        bg = Rectangle(pos=label.pos, size=label.size)
    label.bind(pos=lambda *_: setattr(bg, 'pos', label.pos),
               size=lambda *_: setattr(bg, 'size', label.size))
    Window.add_widget(label)
    Animation(opacity=0, d=0.6).start(label)

    def remove(_dt):
        try:
            Window.remove_widget(label)
        except Exception:
            pass

    Clock.schedule_once(remove, duration)



