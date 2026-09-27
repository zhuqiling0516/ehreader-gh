# -*- coding: utf-8 -*-
"""漫画页面来源：本地文件与在线页面

LocalPage  —— 本地图片文件
OnlinePage —— 在线画廊中的某一页（先用观看页解析出原图直链，再下载）

两者提供统一接口，供 reader.py 的后台线程池使用：
    fetch(fit)      读取（必要时下载）图片，本地 JPEG 会先用 draft 降采样
    str(page)       稳定且唯一的缓存键、进度记录键
    page.display    状态栏/提示里显示的名字

EHentaiGallery 负责解析 e-hentai / exhentai 画廊：
    画廊链接 -> 所有观看页 -> 单页原图直链 -> 图片字节
需要登录才能看的画廊，可在 config.ini 的 [Online] cookie 里配置浏览器 Cookie。
"""

from __future__ import annotations

import html as html_module
import io
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse, urlunparse

import requests
from PIL import Image

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# 代理设置：空/auto = 用系统代理；direct = 直连；其它 = 代理地址
PROXY_AUTO = 'auto'
PROXY_DIRECT = 'direct'
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1', '0.0.0.0'}


def system_proxies() -> dict:
    """读取系统（Windows 注册表）或环境变量里的代理"""
    try:
        found = urllib.request.getproxies()
    except Exception:
        return {}
    proxies = {}
    for scheme in ('http', 'https'):
        value = found.get(scheme) or found.get(scheme.upper())
        if value:
            proxies[scheme] = value
    return proxies


def normalize_proxy(text) -> str:
    """把用户输入规范化：auto / direct / http(s)://主机:端口"""
    value = str(text or '').strip()
    if not value:
        return PROXY_AUTO
    lowered = value.lower()
    if lowered in ('auto', 'system', '系统', '自动'):
        return PROXY_AUTO
    if lowered in ('direct', 'none', 'no', 'off', '0', '直连'):
        return PROXY_DIRECT
    if '://' not in value:
        value = 'http://' + value
    return value


def describe_proxy(setting: str) -> str:
    """给界面/报错用的代理描述"""
    setting = normalize_proxy(setting)
    if setting == PROXY_DIRECT:
        return '直连（不使用代理）'
    if setting == PROXY_AUTO:
        found = system_proxies()
        address = found.get('https') or found.get('http')
        return f'系统代理 {address}' if address else '直连（未检测到系统代理）'
    return f'代理 {setting}'

GALLERY_RE = re.compile(r'/g/(\d+)/([0-9a-zA-Z]+)')
VIEWER_RE = re.compile(r'href="(https?://[^"]*?/s/[0-9a-fA-F]+/\d+-\d+)"')
IMG_TAG_RE = re.compile(r'<img[^>]*\bid=["\']img["\'][^>]*>', re.I)
SRC_RE = re.compile(r'\bsrc=["\']([^"\']+)["\']', re.I)
DATA_SRC_RE = re.compile(r'\bdata-src=["\']([^"\']+)["\']', re.I)
LOAD_IMAGE_RE = re.compile(r"load_image\(\s*'([^']+)'")
TITLE_RE = re.compile(r'<h1[^>]*id=["\'](g[jn])["\'][^>]*>(.*?)</h1>', re.I | re.S)
MAX_HTML_PAGES = 500


def is_online_url(text) -> bool:
    """是否为 http(s) 链接"""
    value = str(text).strip().lower()
    return value.startswith('http://') or value.startswith('https://')


def normalize_gallery_url(url) -> str:
    """把各种写法统一成 https://站点/g/编号/令牌/"""
    value = str(url).strip()
    parsed = urlparse(value)
    match = GALLERY_RE.search(parsed.path or value)
    if not match:
        raise ValueError('不是有效的画廊链接，示例：https://e-hentai.org/g/1234567/0123456789/')
    scheme = parsed.scheme or 'https'
    host = parsed.netloc or 'e-hentai.org'
    return urlunparse((scheme, host, f'/g/{match.group(1)}/{match.group(2)}/', '', '', ''))


class HttpFetcher:
    """带 UA / Cookie / 代理 / 重试的 HTTP 抓取器（站点页面与图床通用）"""

    def __init__(self, cookie: str = '', timeout: int = 30,
                 retries: int = 2, user_agent: str = USER_AGENT, proxy: str = ''):
        self.cookie = (cookie or '').strip()
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        self.proxy_setting = normalize_proxy(proxy)
        self.proxies = self._build_proxies()

    # -- 代理 ----------------------------------------------------------
    def _build_proxies(self) -> dict:
        if self.proxy_setting == PROXY_DIRECT:
            return {'http': None, 'https': None}      # 显式禁用，连环境变量也不走
        if self.proxy_setting == PROXY_AUTO:
            return dict(system_proxies())             # 通常是 Clash 等客户端的系统代理
        return {'http': self.proxy_setting, 'https': self.proxy_setting}

    def _proxies_for(self, url: str) -> dict:
        """本机地址永远直连，不走代理"""
        host = (urlparse(url).hostname or '').lower()
        if host in LOCAL_HOSTS or host.endswith('.local'):
            return {'http': None, 'https': None}
        return self.proxies

    def describe(self) -> str:
        return describe_proxy(self.proxy_setting)

    def headers(self, referer: Optional[str] = None,
                accept: Optional[str] = None) -> dict:
        headers = {
            'User-Agent': self.user_agent,
            'Accept': accept or ('text/html,application/xhtml+xml,'
                                 'image/webp,image/*;q=0.8,*/*;q=0.5'),
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        if self.cookie:
            headers['Cookie'] = self.cookie
        if referer:
            headers['Referer'] = referer
        return headers

    def get(self, url: str, referer: Optional[str] = None,
            retries: Optional[int] = None, binary: bool = False):
        """抓取 URL；binary=True 返回 bytes，否则返回 str"""
        attempts = self.retries if retries is None else retries
        last_error = None
        for attempt in range(attempts + 1):
            try:
                response = requests.get(url, headers=self.headers(referer),
                                        timeout=self.timeout,
                                        proxies=self._proxies_for(url))
                if response.status_code in (429, 509):
                    raise RuntimeError(f'服务器返回 {response.status_code}'
                                       '（访问频率受限，请稍后再试）')
                if response.status_code >= 500:
                    raise RuntimeError(f'服务器返回 {response.status_code}')
                response.raise_for_status()
                return response.content if binary else response.text
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(0.8 * (attempt + 1))
        text = str(last_error)
        if 'SOCKS' in text.upper():
            text = '需要安装 PySocks 才能使用 SOCKS 代理（pip install pysocks），' \
                   '或改用 http:// 形式的代理地址'
        raise RuntimeError(f'请求失败：{text}（{self.describe()}）')

    def text(self, url: str, referer: Optional[str] = None) -> str:
        return self.get(url, referer=referer, binary=False)

    def content(self, url: str, referer: Optional[str] = None) -> bytes:
        return self.get(url, referer=referer, binary=True)



class LocalPage:
    """本地图片文件"""

    kind = 'local'
    error: Optional[str] = None

    def __init__(self, path):
        self.path = Path(path)

    def __str__(self) -> str:
        return str(self.path)

    @property
    def display(self) -> str:
        return self.path.name

    def fetch(self, fit=None) -> Image.Image:
        """打开本地图片；JPEG 先按目标尺寸做 draft 快速降采样"""
        with Image.open(self.path) as source:
            if fit is not None and self.path.suffix.lower() in ('.jpg', '.jpeg', '.jfif'):
                box_w, box_h, _mode, scale = fit
                source.draft('RGB', (max(1, int(box_w * scale)),
                                     max(1, int(box_h * scale))))
            source.load()
            return source


class OnlinePage:
    """在线画廊中的一页"""

    kind = 'online'

    def __init__(self, gallery: 'EHentaiGallery', index: int, viewer_url: str):
        self.gallery = gallery
        self.index = index
        self.viewer_url = viewer_url
        self.error: Optional[str] = None
        self._image_url: Optional[str] = None
        self._lock = threading.Lock()

    def __str__(self) -> str:
        return self.viewer_url

    @property
    def display(self) -> str:
        return f'第 {self.index + 1} 页'

    def image_url(self) -> str:
        """观看页 -> 原图直链（每页只解析一次）"""
        with self._lock:
            if self._image_url is None:
                self._image_url = self.gallery.resolve_image(self.viewer_url)
            return self._image_url

    def fetch(self, fit=None) -> Image.Image:
        self.error = None
        try:
            data = self.gallery.fetch_bytes(self.image_url(), referer=self.viewer_url)
            with Image.open(io.BytesIO(data)) as source:
                if fit is not None and (source.format or '').upper() in ('JPEG', 'MPO'):
                    box_w, box_h, _mode, scale = fit
                    source.draft('RGB', (max(1, int(box_w * scale)),
                                         max(1, int(box_h * scale))))
                source.load()
                return source
        except Exception as exc:
            self.error = str(exc) or exc.__class__.__name__
            raise


class UrlImage:
    """任意直链图片（列表封面、缩略图等），配合 HttpFetcher 使用"""

    kind = 'online'

    def __init__(self, url: str, fetcher: HttpFetcher,
                 referer: Optional[str] = None):
        self.url = url
        self.fetcher = fetcher
        self.referer = referer
        self.error: Optional[str] = None

    def __str__(self) -> str:
        return self.url

    @property
    def display(self) -> str:
        name = Path(urlparse(self.url).path).name
        return name or self.url

    @property
    def empty(self) -> bool:
        """没有可用图片地址时为 True（列表里部分条目没有封面）"""
        return not self.url

    def fetch(self, fit=None) -> Image.Image:
        if not self.url:
            raise RuntimeError('没有封面地址')
        self.error = None
        try:
            data = self.fetcher.content(self.url, self.referer)
            with Image.open(io.BytesIO(data)) as source:
                if fit is not None and (source.format or '').upper() in ('JPEG', 'MPO'):
                    box_w, box_h, _mode, scale = fit
                    source.draft('RGB', (max(1, int(box_w * scale)),
                                         max(1, int(box_h * scale))))
                source.load()
                return source
        except Exception as exc:
            self.error = str(exc) or exc.__class__.__name__
            raise


class EHentaiGallery:
    """e-hentai / exhentai 画廊解析与图片抓取"""
    def __init__(self, url, cookie: str = '', timeout: int = 30, fetcher=None,
                 proxy: str = ''):
        self.url = normalize_gallery_url(url)
        self.fetcher = fetcher or HttpFetcher(cookie=cookie, timeout=timeout,
                                              proxy=proxy)
        self.cookie = self.fetcher.cookie
        self.timeout = self.fetcher.timeout
        self.title = ''
        self.viewer_urls: List[str] = []

    # -- HTTP ----------------------------------------------------------
    def _headers(self, referer: Optional[str] = None) -> dict:
        return self.fetcher.headers(referer)

    def _get(self, url: str, referer: Optional[str] = None,
             retries: int = 2, binary: bool = False):
        return self.fetcher.get(url, referer=referer,
                                retries=retries, binary=binary)

    # -- 画廊解析 ------------------------------------------------------
    def load(self) -> 'EHentaiGallery':
        """解析画廊：标题 + 所有观看页链接"""
        first = self._get(self.url)
        self.title = self._parse_title(first)
        max_page = self._parse_max_page(first)
        seen = set()
        self.viewer_urls = []
        self._collect(first, seen)
        for page in range(1, min(max_page, MAX_HTML_PAGES) + 1):
            page_html = self._get(f'{self.url}?p={page}')
            before = len(self.viewer_urls)
            self._collect(page_html, seen)
            if len(self.viewer_urls) == before:      # 已经没有新内容
                break
        if not self.viewer_urls:
            raise RuntimeError('没有解析到任何图片，请确认链接有效；'
                               '若画廊需要登录，可在 config.ini 的 [Online] 中配置 cookie')
        return self

    def make_pages(self) -> List[OnlinePage]:
        """生成页面列表"""
        return [OnlinePage(self, index, url)
                for index, url in enumerate(self.viewer_urls)]

    @property
    def page_count(self) -> int:
        return len(self.viewer_urls)

    @staticmethod
    def _parse_title(page_html: str) -> str:
        """优先日文标题（#gj），其次英文标题（#gn）"""
        titles = {}
        for key, raw in TITLE_RE.findall(page_html):
            text = html_module.unescape(re.sub(r'<[^>]+>', '', raw)).strip()
            if text and key.lower() not in titles:
                titles[key.lower()] = text
        return titles.get('gj') or titles.get('gn') or ''

    @staticmethod
    def _parse_max_page(page_html: str) -> int:
        numbers = [int(value) for value in re.findall(r'\?p=(\d+)', page_html)]
        return max(numbers) if numbers else 0

    def _collect(self, page_html: str, seen: set) -> None:
        start = page_html.find('id="gdt"')
        block = page_html[start:] if start != -1 else page_html
        for url in VIEWER_RE.findall(block):
            if url not in seen:
                seen.add(url)
                self.viewer_urls.append(url)

    # -- 单页解析 ------------------------------------------------------
    def resolve_image(self, viewer_url: str) -> str:
        """观看页 -> 原图直链"""
        page_html = self._get(viewer_url, referer=self.url)
        tag = IMG_TAG_RE.search(page_html)
        if tag:
            match = SRC_RE.search(tag.group(0)) or DATA_SRC_RE.search(tag.group(0))
            if match:
                return html_module.unescape(match.group(1))
        match = LOAD_IMAGE_RE.search(page_html)
        if match:
            return match.group(1)
        raise RuntimeError('无法获取原图地址，这一页可能需要登录'
                           '（请在 config.ini 的 [Online] 中配置 cookie）')

    def fetch_bytes(self, url: str, referer: Optional[str] = None) -> bytes:
        """下载图片字节"""
        return self._get(url, referer=referer, retries=2, binary=True)

