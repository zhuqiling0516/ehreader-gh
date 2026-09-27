# -*- coding: utf-8 -*-
"""移动端（安卓 / 桌面）数据层

直接复用桌面版已经验证过的 pages.py / browse.py，
只把「配置 / 收藏 / 标签 / 阅读进度」的存储换成手机私有目录下的 JSON 文件。

    安卓：$ANDROID_PRIVATE（应用私有目录）
    桌面：~/.ehreader

所有网络动作都放在线程里执行，结果通过回调交给主线程（Kivy 的 Clock）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
# vendor/ 打包 APK 时由构建脚本放入共享模块（pages.py / browse.py）；
# 桌面预览时直接用项目根目录里的同名模块。
# 用 reversed 插入，保证 PATH_CANDIDATES 里靠前的目录最终优先级更高。
PATH_CANDIDATES = (BASE_DIR / 'vendor', BASE_DIR.parent, BASE_DIR)
for _candidate in reversed(PATH_CANDIDATES):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from browse import (DEFAULT_SELECTED_MASK, EHentaiBrowser, GalleryCard,
                    GalleryListPage, build_search_url)  # noqa: E402
from pages import (EHentaiGallery, HttpFetcher, LocalPage, describe_proxy,
                   is_online_url)  # noqa: E402

IMAGE_EXTS = {'.jpg', '.jpeg', '.jfif', '.png', '.webp', '.bmp',
              '.gif', '.tif', '.tiff', '.avif', '.ico'}
ARCHIVE_EXTS = {'.zip', '.cbz'}
SKIP_NAMES = {'thumbs.db', 'desktop.ini'}
DEFAULT_LOCAL_DIRS = ('/sdcard/Download', '/sdcard/Pictures', '/sdcard/DCIM')


def data_dir() -> Path:
    """手机上的可写目录：安卓用应用私有目录，桌面用 ~/.ehreader"""
    for key in ('ANDROID_PRIVATE', 'ANDROID_APP_PATH', 'ANDROID_ARGUMENT'):
        value = os.environ.get(key)
        if value and Path(value).is_dir():
            return Path(value)
    try:
        path = Path.home() / '.ehreader'
    except Exception:
        path = BASE_DIR / '.ehreader'
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        path = BASE_DIR
    return path


def natural_key(text: str) -> List:
    """自然排序：2.png 排在 10.png 前面"""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', str(text))]


class Store:
    """配置 + 收藏 + 标签收藏 + 阅读进度（单个 JSON 文件）"""

    DEFAULTS = {
        'cookie': '',
        'proxy': '',              # 空 = 跟随系统代理
        'direction': 'rtl',       # 从右到左
        'mode': 'single',         # single / scroll
        'fit': 'contain',         # contain / width
        'gap': 4,                 # 连页间距
        'local_dirs': list(DEFAULT_LOCAL_DIRS),
    }

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else data_dir() / 'mobile_store.json'
        self.lock = threading.RLock()
        self.data: Dict = {'settings': dict(self.DEFAULTS), 'favorites': [],
                           'tags': [], 'progress': {}, 'recent': []}
        self.load()

    # -- 读写 ----------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except Exception:
            return
        if not isinstance(saved, dict):
            return
        settings = dict(self.DEFAULTS)
        settings.update(saved.get('settings') or {})
        self.data['settings'] = settings
        self.data['favorites'] = [item for item in saved.get('favorites', [])
                                  if isinstance(item, dict) and item.get('url')]
        self.data['tags'] = [str(tag) for tag in saved.get('tags', []) if tag]
        self.data['progress'] = dict(saved.get('progress') or {})
        self.data['recent'] = list(saved.get('recent') or [])

    def save(self) -> None:
        with self.lock:
            payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        try:
            self.path.write_text(payload, encoding='utf-8')
        except Exception:
            pass

    # -- 设置 ----------------------------------------------------------
    def setting(self, key: str, default=None):
        return self.data['settings'].get(key, self.DEFAULTS.get(key, default))

    def set_setting(self, key: str, value) -> None:
        self.data['settings'][key] = value
        self.save()

    def cookie(self) -> str:
        return str(self.setting('cookie') or '')

    def proxy(self) -> str:
        return str(self.setting('proxy') or '')

    def fetcher(self, timeout: int = 25) -> HttpFetcher:
        return HttpFetcher(cookie=self.cookie(), proxy=self.proxy(), timeout=timeout)

    def proxy_text(self) -> str:
        return describe_proxy(self.proxy())

    # -- 收藏 ----------------------------------------------------------
    def favorites(self) -> List[dict]:
        return [dict(item) for item in self.data['favorites']]

    @staticmethod
    def key_of(source) -> str:
        text = str(source)
        if is_online_url(text):
            return text.rstrip('/').lower()
        try:
            return str(Path(text).resolve()).lower()
        except Exception:
            return text.lower()

    def is_favorite(self, url) -> bool:
        key = self.key_of(url)
        return any(self.key_of(item.get('url', '')) == key
                   for item in self.data['favorites'])

    def add_favorite(self, card) -> bool:
        if card is None or not getattr(card, 'url', ''):
            return False
        if self.is_favorite(card.url):
            return False
        record = {'url': card.url, 'title': card.title or card.url,
                  'thumb': getattr(card, 'thumb', ''), 'category': card.category,
                  'pages': getattr(card, 'pages', ''), 'uploader': card.uploader,
                  'posted': getattr(card, 'posted', ''),
                  'added': datetime.now().strftime('%Y-%m-%d %H:%M')}
        with self.lock:
            self.data['favorites'].insert(0, record)
        self.save()
        return True

    def remove_favorite(self, url) -> bool:
        key = self.key_of(url)
        with self.lock:
            before = len(self.data['favorites'])
            self.data['favorites'] = [item for item in self.data['favorites']
                                      if self.key_of(item.get('url', '')) != key]
            changed = len(self.data['favorites']) != before
        if changed:
            self.save()
        return changed

    def toggle_favorite(self, card) -> bool:
        if card is None:
            return False
        if self.is_favorite(card.url):
            self.remove_favorite(card.url)
            return False
        self.add_favorite(card)
        return True

    def favorite_cards(self) -> List[GalleryCard]:
        cards = []
        for item in self.favorites():
            cards.append(GalleryCard(
                url=str(item.get('url', '')),
                title=str(item.get('title') or item.get('url', '')),
                category=str(item.get('category', '')),
                pages=str(item.get('pages', '')),
                uploader=str(item.get('uploader', '')),
                posted=str(item.get('added', '')),
                thumb=str(item.get('thumb', ''))))
        return cards

    # -- 标签收藏 -------------------------------------------------------
    def tags(self) -> List[str]:
        return list(self.data['tags'])

    def add_tags(self, tags) -> int:
        added = 0
        with self.lock:
            for tag in tags or ():
                text = str(tag).strip()
                if text and text not in self.data['tags']:
                    self.data['tags'].append(text)
                    added += 1
        if added:
            self.save()
        return added

    def remove_tag(self, tag) -> bool:
        text = str(tag).strip()
        with self.lock:
            if text in self.data['tags']:
                self.data['tags'].remove(text)
                self.save()
                return True
        return False

    def clear_tags(self) -> None:
        with self.lock:
            self.data['tags'] = []
        self.save()

    # -- 阅读进度 / 最近 --------------------------------------------------
    def progress_of(self, source) -> dict:
        if source is None:
            return {}
        return dict(self.data['progress'].get(self.key_of(source)) or {})

    def save_progress(self, source, index: int, total: int, mode: str) -> None:
        if source is None:
            return
        with self.lock:
            self.data['progress'][self.key_of(source)] = {
                'index': int(index), 'total': int(total), 'mode': mode,
                'updated': datetime.now().strftime('%Y-%m-%d %H:%M')}
        self.save()

    def push_recent(self, source, title: str) -> None:
        if source is None:
            return
        entry = {'url': str(source), 'title': title or str(source)}
        key = self.key_of(source)
        with self.lock:
            self.data['recent'] = [item for item in self.data['recent']
                                   if self.key_of(item.get('url', '')) != key]
            self.data['recent'].insert(0, entry)
            del self.data['recent'][15:]
        self.save()

    def recent(self) -> List[dict]:
        return [dict(item) for item in self.data['recent']]


class Library:
    """搜索 / 打开漫画 / 本地目录

    网络动作全部在线程里跑；结果通过 dispatcher 回到主线程
    （Kivy 界面传入 Clock.schedule_once，测试里用直通函数）。
    """

    def __init__(self, store: Store, dispatcher=None):
        self.store = store
        self._browser: Optional[EHentaiBrowser] = None
        self._dispatch = dispatcher or (lambda func: func())

    def _deliver(self, func, *args) -> None:
        self._dispatch(lambda: func(*args))

    # -- 搜索 ----------------------------------------------------------
    def browser(self) -> EHentaiBrowser:
        """按当前 Cookie / 代理新建（设置在设置页改完立刻生效）"""
        self._browser = EHentaiBrowser(cookie=self.store.cookie(),
                                       proxy=self.store.proxy())
        return self._browser

    def search_url(self, keyword='', tags='', exclude='', language='',
                   source='search', selected_mask=DEFAULT_SELECTED_MASK,
                   page=0) -> str:
        return build_search_url(keyword=keyword, includes=[tags], excludes=[exclude],
                                selected_mask=selected_mask, language=language,
                                source=source, page=page)

    def search_async(self, url: str, on_done: Callable[[GalleryListPage], None],
                     on_error: Callable[[str], None]) -> None:
        def worker():
            try:
                page = self.browser().search(url)
            except Exception as exc:
                self._deliver(on_error, str(exc) or exc.__class__.__name__)
                return
            self._deliver(on_done, page)
        threading.Thread(target=worker, daemon=True, name='eh-search').start()

    # -- 打开漫画 -------------------------------------------------------
    def open_async(self, source, on_done: Callable[[list, str], None],
                   on_error: Callable[[str], None]) -> None:
        """source 可以是在线画廊链接，也可以是本地文件夹 / 单张图片"""
        def worker():
            try:
                if is_online_url(source):
                    gallery = EHentaiGallery(str(source),
                                             cookie=self.store.cookie(),
                                             proxy=self.store.proxy()).load()
                    pages = gallery.make_pages()
                    title = gallery.title or str(source)
                else:
                    path = Path(str(source))
                    files = self.scan_folder(path)
                    if not files:
                        raise RuntimeError(f'目录里没有找到图片：{path}')
                    pages = [LocalPage(item) for item in files]
                    title = path.stem if path.is_file() else path.name
            except Exception as exc:
                self._deliver(on_error, str(exc) or exc.__class__.__name__)
                return
            self._deliver(on_done, pages, title)
        threading.Thread(target=worker, daemon=True, name='eh-open').start()

    @staticmethod
    def scan_folder(folder) -> List[Path]:
        """收集目录（含一层子目录）里的图片，自然排序"""
        def images_of(directory: Path) -> List[Path]:
            found = [item for item in directory.iterdir()
                     if item.is_file() and item.suffix.lower() in IMAGE_EXTS
                     and item.name.lower() not in SKIP_NAMES]
            found.sort(key=lambda item: natural_key(item.name))
            return found

        folder = Path(str(folder))
        if folder.is_file():
            return [folder]
        if not folder.is_dir():
            return []
        found = images_of(folder)
        if found:
            return found
        try:
            subdirs = [item for item in folder.iterdir() if item.is_dir()]
        except Exception:
            return []
        for sub in sorted(subdirs, key=lambda item: natural_key(item.name)):
            found.extend(images_of(sub))
        return found

    def local_choices(self) -> List[Path]:
        """设置里配置的本地目录中，看起来像漫画的文件夹/压缩包"""
        choices: List[Path] = []
        for root in self.store.setting('local_dirs') or ():
            directory = Path(str(root))
            if not directory.is_dir():
                continue
            if self.scan_folder(directory):
                choices.append(directory)
            try:
                subs = sorted((item for item in directory.iterdir()),
                              key=lambda item: natural_key(item.name))[:80]
            except Exception:
                continue
            for sub in subs:
                if sub.is_dir() and self.scan_folder(sub):
                    choices.append(sub)
                elif sub.is_file() and sub.suffix.lower() in ARCHIVE_EXTS:
                    choices.append(sub)
        return choices[:100]


