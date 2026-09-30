# -*- coding: utf-8 -*-
"""Kivy 通用控件：异步取图、列表行、提示条、统一主题

取图流程（安卓与桌面一致）：
    后台线程 取字节 → 解码成 Kivy CoreImage → 主线程贴到 Image
      · webp：走 Kivy 的 SDL2 provider（libSDL2_image 编进了 webp）
      · 其它：走 Pillow 解码并缩到 max_side，省显存

注意：p4a 里 Pillow 没有 libwebp（设备上只有 _imaging*.so，没有 _webp.so），
webp 交给 Pillow 会直接失败，详见 decode_texture 的注释。
"""

from __future__ import annotations

import io
import threading
import time
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
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label

from PIL import Image as PILImage

try:                                    # 老版本 Kivy 没有圆角矩形，退化成直角即可
    from kivy.graphics import RoundedRectangle
except ImportError:                     # pragma: no cover
    RoundedRectangle = None

POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix='mobile-img')
TEXTURES: "OrderedDict[tuple, object]" = OrderedDict()
TEXTURE_LIMIT = 12          # 一页正文 1280x1920 ≈ 9.8MB 显存，缓存留太多会被显存/GC 拖死
_lock = threading.Lock()

# ----------------------------------------------------------------------
# 滚动闸门：滚动中不建纹理
# ----------------------------------------------------------------------
# CoreImage / SDL2 解码必须在主线程做，而一张 1280x1920 的 webp 解一次要几十毫秒。
# 滚动过程中每来一张图就地解一次，手指就会一顿一顿的。所以滚动/甩动期间先把
# 「解码 + 建纹理」排队，等滚动停下来再一张一张放行（间隔放，避免连解一串又卡住）。
#
# 门槛用「截止时间」而不是布尔量：调用方（阅读器）万一没来得及清标志，
# 闸门也会自己过期，绝不会把后面的取图永久卡在队列里。
_upload_gate = {'until': 0.0, 'queue': [], 'ticking': False}
UPLOAD_HOLD = 0.22          # 最后一次滚动事件之后再等这么久才放行
UPLOAD_INTERVAL = 0.09      # 放行间隔：每隔这么久解一张，主线程还有空处理触摸


def mark_scrolling(hold: float = UPLOAD_HOLD) -> None:
    """滚动中调用：图片解码先排队，等手指停下再放行"""
    _upload_gate['until'] = time.monotonic() + hold
    if not _upload_gate['ticking']:
        _upload_gate['ticking'] = True
        Clock.schedule_once(_gate_tick, hold)


def set_upload_busy(busy: bool) -> None:
    """True = 按 mark_scrolling() 处理；False = 立刻放行队列"""
    if busy:
        mark_scrolling()
    else:
        _upload_gate['until'] = 0.0
        _drain_uploads()


def pending_uploads() -> int:
    return len(_upload_gate['queue'])


def _gate_busy() -> bool:
    return time.monotonic() < _upload_gate['until']


def _gate_tick(_dt) -> None:
    _upload_gate['ticking'] = False
    _drain_uploads()


def _drain_uploads() -> None:
    """放行排队中的解码；还有剩余就隔一会儿再放，别一次全解完"""
    queue = _upload_gate['queue']
    if _gate_busy():
        if queue and not _upload_gate['ticking']:
            _upload_gate['ticking'] = True
            Clock.schedule_once(
                _gate_tick, max(0.01, _upload_gate['until'] - time.monotonic()))
        return
    if not queue:
        return
    job = queue.pop(0)
    try:
        job()
    except Exception as exc:                    # noqa: BLE001
        _warn('_drain_uploads: %r' % (exc,))
    if queue and not _upload_gate['ticking']:
        _upload_gate['ticking'] = True
        Clock.schedule_once(_gate_tick, UPLOAD_INTERVAL)


# ----------------------------------------------------------------------
# 统一主题：扁平深色 + 圆角卡片
# ----------------------------------------------------------------------
THEME = {
    'bg':       (0.07, 0.075, 0.09, 1),      # 窗口底色
    'panel':    (0.12, 0.13, 0.155, 1),      # 顶栏/底栏/面板
    'card':     (0.155, 0.165, 0.195, 1),    # 列表行卡片
    'card_sel': (0.20, 0.28, 0.40, 1),       # 选中行
    'field':    (0.085, 0.09, 0.11, 1),      # 输入框底
    'accent':   (0.235, 0.55, 0.86, 1),      # 主题色（主按钮/选中态）
    'accent_d': (0.16, 0.30, 0.44, 1),       # 主题色暗调（次按钮）
    'button':   (0.20, 0.215, 0.25, 1),      # 普通按钮
    'button_d': (0.28, 0.30, 0.35, 1),       # 按下
    'text':     (0.94, 0.95, 0.96, 1),
    'dim':      (0.62, 0.65, 0.70, 1),
    'star':     (0.95, 0.76, 0.25, 1),
    'radius':   dp(9),
}


def paint_round(widget, color, radius=None):
    """给控件画一层圆角背景（放在 canvas.before，跟随控件位置尺寸）

    返回 (Color 指令, 矩形指令)，需要动态改色时可持有它。
    """
    radius = radius if radius is not None else THEME['radius']
    with widget.canvas.before:
        fill = Color(*color)
        if RoundedRectangle is not None:
            rect = RoundedRectangle(pos=widget.pos, size=widget.size,
                                    radius=[radius] * 4)
        else:
            rect = Rectangle(pos=widget.pos, size=widget.size)
    widget.bind(pos=lambda *_: setattr(rect, 'pos', widget.pos),
                size=lambda *_: setattr(rect, 'size', widget.size))
    return fill, rect


def flat_input(field=None, **kwargs):
    """统一风格的输入框（深底、浅字、圆角）"""
    from kivy.uix.textinput import TextInput
    kwargs.setdefault('background_normal', '')
    kwargs.setdefault('background_active', '')
    kwargs.setdefault('background_color', (0, 0, 0, 0))
    kwargs.setdefault('foreground_color', THEME['text'])
    kwargs.setdefault('hint_text_color', THEME['dim'])
    kwargs.setdefault('cursor_color', THEME['accent'])
    kwargs.setdefault('selection_color', (0.235, 0.55, 0.86, 0.35))
    kwargs.setdefault('padding', (dp(10), dp(8)))
    field = TextInput(**kwargs) if field is None else field
    paint_round(field, THEME['field'], dp(8))
    # 关键：paint_round 往 canvas.before 里追加了一个「输入框底色」的 Color 指令。
    # 而 Kivy 的 <TextInput> 样式规则（kivy/data/style.kv）是把**文字颜色**放在
    # canvas.before 里的，文字矩形却由 _update_graphics() 加到主 canvas：
    #     canvas.before:  Color(rgba: hint_text_color if not text else foreground_color)
    #     canvas:         Rectangle(texture: 文字纹理)
    # canvas.before 会在主 canvas 之前立刻生效，所以底色那条 Color 一追加进去，
    # 就把文字颜色顶掉了 —— 文字被画成和输入框底色一模一样，看上去就是
    # 「输入框里什么都不显示」（实测那块区域只有背景色 + 圆角抗锯齿，没有一个文字像素）。
    # 所以在底色之后补回一条文字颜色，并跟随内容/焦点/禁用状态同步。
    with field.canvas.before:
        text_color = Color(*THEME['dim'])

    def _sync_text_color(*_args):
        if field.disabled:
            text_color.rgba = (0.45, 0.47, 0.50, 0.5)
        elif field.text:
            text_color.rgba = THEME['text']
        else:
            text_color.rgba = THEME['dim']

    field.bind(text=_sync_text_color, focus=_sync_text_color,
               disabled=_sync_text_color)
    _sync_text_color()
    return field


def make_label(text: str = '', font_size=sp(13), color=(1, 1, 1, 1), **kwargs):
    """带自动折行/省略的 Label（Kivy 需要手动把 text_size 绑到自身尺寸）"""
    label = Label(text=text, font_size=font_size, color=color,
                  halign='left', valign='middle', **kwargs)
    label.bind(size=lambda widget, size: setattr(widget, 'text_size',
                                                 (size[0], size[1])))
    return label


def _sniff_ext(data: bytes) -> str:
    """按魔数判断图片格式"""
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    if data[:2] == b'\xff\xd8':
        return 'jpg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data[:2] == b'BM':
        return 'bmp'
    return ''


def _flag(name: str) -> bool:
    """设备上存在 files/<name> 就打开对应调试开关（不用重新打包）"""
    try:
        import os
        return os.path.exists(os.path.join(
            os.environ.get('ANDROID_PRIVATE') or '.', name))
    except Exception:                           # noqa: BLE001
        return False


# 每张图都写一行日志 + 读 texture.pixels 是很贵的：
# Texture.pixels 会临时建 Fbo 再 glReadPixels，一张 1280x1920 就是 9.8MB 的回读，
# 放在贴图路径上会让滚动明显发顿。所以默认全关，要排查时再开：
#     adb shell run-as org.ehreader.ehreader touch files/debug_img     （图片链路日志）
#     adb shell run-as org.ehreader.ehreader touch files/debug_pixels  （额外采样像素）
DEBUG_IMG = _flag('debug_img')
DEBUG_PIXELS = _flag('debug_pixels')
# 后台解码默认开启：实测把正文页的主线程耗时从 155ms（中位）压到 5ms。
# 万一在别的机型上出问题，设备上放一个 files/no_offload_decode 就能退回老路径，
# 不用重新打包：
#     adb shell run-as org.ehreader.ehreader touch files/no_offload_decode
OFFLOAD_DECODE = not _flag('no_offload_decode')


def _log(*parts) -> None:
    """把图片链路的调试信息写到 files/dbg.log（默认关闭，见 DEBUG_IMG）

    （安卓上 stdout→logcat 偶发失效；写文件后用
      adb shell run-as org.ehreader.ehreader cat files/dbg.log 一定能读到）
    """
    if not DEBUG_IMG:
        return
    _write_log(' '.join(str(p) for p in parts))


def _warn(*parts) -> None:
    """出问题的少量日志：永远写（失败才发生，开销可忽略）"""
    _write_log(' '.join(str(p) for p in parts))


def _write_log(text: str) -> None:
    try:
        import os
        with open(os.path.join(os.environ.get('ANDROID_PRIVATE') or '.',
                               'dbg.log'), 'a', encoding='utf-8') as handle:
            handle.write('[img] ' + text + '\n')
    except Exception:                           # noqa: BLE001
        pass


if OFFLOAD_DECODE:
    # 先把 SDL_image 初始化掉，免得第一次解码是在工作线程里做初始化
    try:
        from kivy.core.image import _img_sdl2 as _sdl2_loader
        _sdl2_loader.init()
    except Exception as _exc:                   # noqa: BLE001
        OFFLOAD_DECODE = False
        _warn('offload_decode 不可用，退回主线程解码: %r' % (_exc,))


def decode_pixels(data: bytes):
    """【后台线程可用】只解码、不建纹理：交给 Kivy 的 SDL2 解码器吐出原始像素。

    为什么 CoreImage 不能在后台线程碰，而这个可以：CoreImage 会创建 Texture
    （GL 调用），在非主线程做会把 SDL/GL 上下文搞坏（实测直接 SIGSEGV 在
    opengl_utils 的 strlen 上）。而 _img_sdl2.load_from_memory 只是
    SDL_image 解码 + 格式转换，全程不碰 GL，所以可以放进工作线程。

    返回 (w, h, fmt, pixels, rowlength)，和 Kivy 内部 ImageData 用的是同一套数据。
    """
    from kivy.core.image import _img_sdl2
    _img_sdl2.init()                     # 幂等；保证 IMG_Init 已跑过
    info = _img_sdl2.load_from_memory(bytes(data))
    if not info:
        raise ValueError('SDL2 解码失败')
    return info


def texture_from_pixels(info):
    """【只能在主线程调用】原始像素 → Kivy Texture

    和 Kivy 自己 CoreImage 的落地位完全一致（ImageData + create_from_data
    + flip_vertical），所以画面朝向/行距都不会变。
    """
    from kivy.core.image import ImageData
    from kivy.graphics.texture import Texture
    width, height, fmt, pixels, rowlength = info
    data = ImageData(width, height, fmt, pixels, rowlength=rowlength)
    texture = Texture.create_from_data(data, mipmap=False)
    if data.flip_vertical:
        texture.flip_vertical()
    return texture


def prepare_image(data: bytes, max_side: int = 1600):
    """【后台线程可用】纯 CPU 的图片准备工作。

    返回 (字节, 扩展名)，两者交给主线程的 build_texture 去建纹理；
    开了 OFFLOAD_DECODE 时改为返回 ('pixels', (w,h,fmt,pixels,rowlength))，
    即解码已经在后台做完，主线程只剩上传。

    webp 原样返回（交给主线程用 SDL2 解码：p4a 的 Pillow 没编 libwebp）；
    其它格式用 Pillow 解码并缩到 max_side，再转成 PNG 字节，省显存。

    注意：这里绝对不能调用 Kivy 的 CoreImage / SDL_image 建纹理那一套。
    实测在后台线程里调 CoreImage 会破坏 SDL/GL 的上下文状态，随后主线程创建
    纹理时 glGetString 返回 NULL，在 opengl_utils 的 strlen 上直接 SIGSEGV：
        #00 libc.so (__strlen_aarch64) ← strlen(NULL)
        #01-#03 kivy/graphics/opengl_utils.so
        #04-#05 kivy/graphics/texture.so
    所以解码留在后台（Pillow / SDL_image 解码器都是纯 CPU、不碰 GL），
    纹理创建一律回主线程。
    """
    if not data:
        raise ValueError('空图片数据')
    ext = _sniff_ext(data)
    _log('prepare_image: %d bytes, sniff=%r, offload=%s' % (
        len(data), ext, OFFLOAD_DECODE))
    if ext == 'webp':
        if OFFLOAD_DECODE:
            try:
                return 'pixels', decode_pixels(data)
            except Exception as exc:            # noqa: BLE001
                _warn('prepare_image: 后台解码 webp 失败，退回主线程解: %r' % (exc,))
        return data, 'webp'
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
    if OFFLOAD_DECODE:
        # 后台已经是原图了，直接给像素；省掉「编码成 PNG → 主线程再解回来」这一轮
        rgba = image.convert('RGBA')
        return 'pixels', (rgba.width, rgba.height, 'rgba', rgba.tobytes(),
                          rgba.width * 4)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue(), 'png'


def build_texture(prepared):
    """【只能在主线程调用】prepared → Kivy Texture（含纹理创建/上传）

    返回的就是 Texture 本身（不再是 CoreImage 包装），缓存里存的也是它。
    """
    if not prepared:
        _log('build_texture: prepared 为空')
        return None
    if prepared[0] == 'pixels':
        return texture_from_pixels(prepared[1])
    data, ext = prepared
    try:
        core = CoreImage(io.BytesIO(data), ext=ext)
    except Exception as exc:                    # noqa: BLE001
        _warn('build_texture: CoreImage(%s) 抛异常: %r' % (ext, exc))
        raise
    texture = None
    try:
        texture = core.texture
    except Exception as exc:                    # noqa: BLE001
        _warn('build_texture: 取 texture 抛异常: %r' % (exc,))
    _log('build_texture: ext=%s core=%s texture=%s' % (
        ext, core is not None, texture is not None))
    if texture is not None and DEBUG_PIXELS:
        # 判定解码是不是空的：全 0 = 透明空纹理（贴上去就是一片白底）。
        # 角上像素可能是白边，所以同时采画面正中一行，才能判断真的有没有内容。
        # 注意 texture.pixels 非常贵（临时 Fbo + glReadPixels），只在排查时开。
        try:
            px = texture.pixels
            w, h = int(texture.width), int(texture.height)
            mid = ((h // 2) * w + (w // 2)) * 4
            _log('build_texture: tex=%dx%d colorfmt=%s corner=%s mid=%s' % (
                w, h, texture.colorfmt,
                [int(v) for v in px[:12]],
                [int(v) for v in px[mid:mid + 12]]))
        except Exception as exc:                # noqa: BLE001
            _warn('build_texture: 读 pixels 失败: %r' % (exc,))
    return texture


def decode_texture(data: bytes, max_side: int = 1600):
    """字节流 → Kivy Texture（便捷函数，只能在主线程调用）"""
    return build_texture(prepare_image(data, max_side))


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
        # Kivy 2.3 起 allow_stretch/keep_ratio 已废弃（会刷警告，而且 2.3.1 上的
        # 别名实现容易被误用导致渲染异常），优先转成 fit_mode；老 Kivy 再回退旧属性。
        stretch = kwargs.pop('allow_stretch', None)
        keep = kwargs.pop('keep_ratio', None)
        if 'fit_mode' not in kwargs and (stretch or keep):
            kwargs['fit_mode'] = 'contain' if keep else 'stretch'
        try:
            super().__init__(**kwargs)
        except TypeError:                       # 老版本 Kivy 没有 fit_mode
            kwargs.pop('fit_mode', None)
            if stretch is not None:
                kwargs['allow_stretch'] = stretch
            if keep is not None:
                kwargs['keep_ratio'] = keep
            super().__init__(**kwargs)
        self.key = key
        self.loader = loader
        self.max_side = max_side
        self._token = 0
        self._pending_key = None
        self.max_retries = 2          # 网络抖动（SSLEOF/超时）导致的失败自动重来
        self.on_fail = None           # 彻底失败时的回调 (key) -> None
        self.gave_up = False          # 重试完仍失败：阅读器据此不再反复重发请求

    def start(self, attempt: int = 0) -> bool:
        """开始加载；返回 True 表示需要等待（缓存未命中）"""
        if self.key is None or self.loader is None:
            return False
        texture = cached_texture(self.key)
        if texture is not None:
            # 缓存命中：纹理还在显存里，直接贴上，不做任何解码
            self.texture = texture
            return False
        if self._pending_key == self.key:
            return True                  # 同一张图正在解码，不要重复发请求
        self._token += 1
        token = self._token
        key, loader, max_side = self.key, self.loader, self.max_side
        self._pending_key = key
        self.gave_up = False

        def worker():
            # 后台只做纯 CPU 工作：取字节 + 解码/缩放
            try:
                prepared = prepare_image(loader(), max_side)
            except Exception as exc:            # noqa: BLE001
                _warn('worker: prepare_image 失败: %r (key=%s, 第%d次)' % (
                    exc, key, attempt + 1))
                prepared = None

            def apply(_dt):
                # 主线程：CoreImage / 纹理创建必须在这里做（否则 SDL/GL 状态被破坏 → 段错误）
                if self._pending_key == key:
                    self._pending_key = None
                if prepared is None:
                    self._on_missing(key, token, attempt)
                    return
                if _gate_busy():
                    # 手指还在滚/甩：先排队，等滚动停下再解码上传（见 mark_scrolling）
                    _upload_gate['queue'].append(
                        lambda: self._commit(prepared, key, token, attempt))
                    return
                self._commit(prepared, key, token, attempt)

            Clock.schedule_once(apply, 0)

        POOL.submit(worker)
        return True

    # -- 主线程回调 ----------------------------------------------------
    def _on_missing(self, key, token, attempt) -> None:
        """取字节/解码失败（实测出现过 SSLEOFError、连接被掐断）"""
        if self._token != token:
            return
        if attempt < self.max_retries:
            # 不重试的话这一页就永远空白，翻回来也不会再加载
            _warn('apply: 第%d次失败，%.1fs 后重试 (key=%s)' % (
                attempt + 1, 1.0 + attempt, key))
            Clock.schedule_once(lambda _d: self.start(attempt + 1), 1.0 + attempt)
            return
        _warn('apply: 放弃加载 (key=%s)' % (key,))
        self.gave_up = True
        if self.on_fail is not None:
            try:
                self.on_fail(key)
            except Exception:                   # noqa: BLE001
                pass

    def _commit(self, prepared, key, token, attempt=0) -> None:
        """【主线程】建纹理 + 贴图（贵，所以要在滚动间隙做）

        开了 OFFLOAD_DECODE 时这里只剩拷贝 + 上传；没开时解码也在这一步里。
        """
        if token != self._token and cached_texture(key) is not None:
            return                               # 已经切图且有缓存，不必再解一遍
        started = time.perf_counter()
        texture = None
        try:
            texture = build_texture(prepared)
        except Exception as exc:                # noqa: BLE001
            _warn('apply: build_texture 失败: %r' % (exc,))
            texture = None
        cost = (time.perf_counter() - started) * 1000.0
        if texture is None:
            self._on_missing(key, token, attempt)
            return
        # 关键顺序：先无条件入缓存，再决定要不要贴到界面。
        # 入缓存放在 token 检查之后会出大问题：同一张图被反复 reload 时，
        # token 过期把结果丢掉 → 缓存永远空 → 每次都重新下载解码 → 死循环、白页。
        store_texture(key, texture)
        if token != self._token:
            _log('apply: 已入缓存但界面已切图 (key=%s)' % (key,))
            return
        try:
            self.texture = texture
        except Exception as exc:                # noqa: BLE001
            _warn('apply: 赋 texture 失败: %r' % (exc,))
            self.texture = None
        _log('apply: key=%s texture=%s widget_size=%s 主线程耗时=%.0fms' % (
            key, self.texture is not None,
            tuple(round(v) for v in self.size), cost))

    def cancel(self) -> None:
        self._token += 1


class GalleryRow(ButtonBehavior, BoxLayout):
    """漫画列表里的一行：封面 + 标题 + 元信息 + 标签 + ★"""

    def __init__(self, reader_app, card, on_select=None, on_open=None, **kwargs):
        super().__init__(orientation='horizontal', size_hint_y=None,
                         height=dp(104), padding=(dp(10), dp(8)),
                         spacing=dp(10), **kwargs)
        self.app = reader_app
        self.card = card
        self.on_select = on_select
        self.on_open = on_open

        with self.canvas.before:
            self._bg_color = Color(*THEME['card'])
            if RoundedRectangle is not None:
                self._bg = RoundedRectangle(pos=self.pos, size=self.size,
                                            radius=[THEME['radius']] * 4)
            else:
                self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg,
                  on_press=self._pressed, on_release=self._released)

        cover_box = BoxLayout(size_hint=(None, 1), width=dp(72))
        with cover_box.canvas.before:
            Color(*THEME['field'])
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

        # 星标做成可点的按钮：列表里直接能收藏，不必先在底栏选中再点收藏
        self.star = Button(text='★' if reader_app.store.is_favorite(card.url) else '☆',
                           font_size=sp(18), size_hint=(None, 1), width=dp(34),
                           background_normal='', background_down='',
                           background_color=(0, 0, 0, 0), color=THEME['star'])
        self.star.bind(on_release=lambda *_: self._toggle_star())
        self.add_widget(self.star)
        self.selected = False

    def _toggle_star(self) -> None:
        added = self.app.store.toggle_favorite(self.card)
        self.refresh_star()
        try:
            toast(self, '已收藏：%s' % (self.card.title or self.card.url) if added
                  else '已取消收藏')
            self.app.refresh_favorites()
        except Exception:                       # noqa: BLE001
            pass

    # -- 外观 ----------------------------------------------------------
    def _sync_bg(self, *_args) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size

    def set_selected(self, flag: bool) -> None:
        self.selected = flag
        self._bg_color.rgba = THEME['card_sel'] if flag else THEME['card']

    def refresh_star(self) -> None:
        self.star.text = '★' if self.app.store.is_favorite(self.card.url) else '☆'

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
        # 按下即时高亮。但滚动时 ScrollView 会抢走后续触摸，行收不到 on_release，
        # 划过去的行就会一直亮着；所以每次按下先按「当前选中行」把同屏其他行刷一遍。
        screen = getattr(self.on_select, '__self__', None)
        rows = getattr(screen, 'rows', ())
        if rows:
            selected = getattr(screen, 'selected', None)
            for other in rows:
                if other is not self:
                    other.set_selected(other is selected)
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
    print('[dbg] toast: %s' % text)
    label = Label(text=text, font_size=sp(12), size_hint=(None, None),
                  color=(1, 1, 1, 0.96), padding=(dp(12), dp(8)),
                  halign='center', valign='middle')
    # 注意别给它 disabled=True：Kivy 里「disabled 且被点到」的控件会 return True
    # 直接吞掉这次触摸；保持 enabled 的 Label 不会拦触摸，事件会继续往下分发。
    max_width = max(dp(200), Window.width - dp(60))
    label.text_size = (max_width, None)          # 过长的提示自动换行
    label.bind(texture_size=lambda widget, size:
               setattr(widget, 'size', (size[0], size[1])))
    label.bind(size=lambda widget, size:
               setattr(widget, 'pos', ((Window.width - size[0]) / 2, dp(60))))
    with label.canvas.before:
        Color(0.10, 0.11, 0.135, 0.95)
        if RoundedRectangle is not None:
            bg = RoundedRectangle(pos=label.pos, size=label.size,
                                  radius=[dp(10)] * 4)
        else:
            bg = Rectangle(pos=label.pos, size=label.size)
    label.bind(pos=lambda *_: setattr(bg, 'pos', label.pos),
               size=lambda *_: setattr(bg, 'size', label.size))
    Window.add_widget(label)
    # 先完整显示 duration 秒，最后 0.6 秒才淡出。
    # （原来这里直接就 Animation(...).start()，等于弹出即淡出，
    #   2.4 秒里有 1.8 秒是「看不见但还在原地挡触摸」的状态。）
    Clock.schedule_once(
        lambda _dt: Animation(opacity=0, d=0.6).start(label),
        max(0.1, duration - 0.6))

    def remove(_dt):
        try:
            Window.remove_widget(label)
        except Exception:
            pass

    Clock.schedule_once(remove, duration)
