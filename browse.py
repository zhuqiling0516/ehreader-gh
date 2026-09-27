# -*- coding: utf-8 -*-
"""e-hentai / exhentai 画廊列表与搜索

    EHentaiBrowser.build_search_url(...)  构建搜索/首页/热门链接
    EHentaiBrowser.search(url)            抓取并解析列表页 -> GalleryListPage

列表页解析结果：
    GalleryListPage.cards      每个条目：链接、标题、分类、标签、页数、评分、封面、上传者
    GalleryListPage.next_url   下一页链接（跟随站点自身分页，保留全部查询参数）
    GalleryListPage.prev_url   上一页链接

分类筛选使用站点的 f_cats 位掩码（提交的是「被排除」的分类）。
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlencode, urljoin, urlparse, urlunparse, parse_qs

from pages import HttpFetcher

BASE_URL = 'https://e-hentai.org/'
POPULAR_URL = 'https://e-hentai.org/popular'

# 分类：(键, 显示名, 位掩码)
CATEGORIES: List[Tuple[str, str, int]] = [
    ('doujinshi', '同人志', 2),
    ('manga', '漫画', 4),
    ('artistcg', '画师CG', 8),
    ('gamecg', '游戏CG', 16),
    ('imageset', '图集', 256),
    ('cosplay', 'Cosplay', 64),
    ('asianporn', '亚洲', 32),
    ('western', '欧美', 512),
    ('misc', '杂项', 1),
    ('nonh', '全年龄', 1024),
]
ALL_CATS_MASK = sum(bit for _key, _label, bit in CATEGORIES)
# 站点默认（不传 f_cats 时）排除「杂项」和「全年龄」
DEFAULT_EXCLUDED_MASK = 1 | 1024
DEFAULT_SELECTED_MASK = ALL_CATS_MASK & ~DEFAULT_EXCLUDED_MASK

SOURCE_LABELS = {'search': '搜索', 'front': '首页', 'popular': '热门'}
LANGUAGES = [('', '不限'), ('chinese', '中文'), ('japanese', '日文'),
             ('english', '英文'), ('korean', '韩文')]

# --- 列表页解析用正则 -----------------------------------------------------
# 真实布局：<table class="itg gltc"> 内每行一个画廊，行本身没有 class；
# 旧布局：<tr class="gtr0">，两种都支持（用「行内含画廊链接」来过滤）。
GALLERY_HREF_RE = re.compile(r'href="([^"]*?/g/\d+/[0-9a-zA-Z]+/)"')
DIV_RE = {
    'title': re.compile(r'<div[^>]*class="[^"]*\bglink\b[^"]*"[^>]*>(.*?)</div>',
                        re.I | re.S),
    'category': re.compile(r'<div[^>]*class="[^"]*\bcn\b[^"]*"[^>]*>(.*?)</div>',
                           re.I | re.S),
    'cut': re.compile(r'<div[^>]*class="[^"]*\bglcut\b[^"]*"[^>]*>(.*?)</div>',
                      re.I | re.S),
    'uploader': re.compile(r'<div[^>]*class="[^"]*\bglname\b[^"]*"[^>]*>(.*?)</div>',
                           re.I | re.S),
}
GT_DIV_RE = re.compile(r'<div[^>]*class="[^"]*\bgt\b[^"]*"[^>]*>(.*?)</div>', re.I | re.S)
IMG_RE = re.compile(r'<img[^>]*>', re.I)
DATA_SRC_RE = re.compile(r'data-src="([^"]+)"', re.I)
SRC_RE = re.compile(r'\bsrc="([^"]+)"', re.I)
RATING_RE = re.compile(r'<div[^>]*class="[^"]*\bir\b[^"]*"[^>]*title="([^"]*)"', re.I)
PAGES_RE = re.compile(r'>\s*(\d[\d,]*)\s*pages?\s*<', re.I)
UPLOADER_RE = re.compile(r'href="[^"]*?/uploader/([^"?/]+)"', re.I)
POSTED_RE = re.compile(r'id="posted_\d+">([^<]*)<', re.I)
ANCHOR_RE = re.compile(r'<a[^>]*>(.*?)</a>', re.I | re.S)
HTML_TAG_RE = re.compile(r'<[^>]+>')
ANCHOR_TAG_RE = re.compile(r'<a[^>]*id="(dnext|dprev)"[^>]*>', re.I)
HREF_RE = re.compile(r'href="([^"]*)"', re.I)
LOAD_PAGE_RE = re.compile(r'load_(?:next|prev)_page\((\d+)\)', re.I)
# 站点用 JS 变量给出上一页/下一页（?next=<gid> 形式的游标分页）
JS_PAGER_RE = re.compile(r'var\s+(prev|next)url\s*=\s*"([^"]*)"', re.I)
SEARCHTEXT_RE = re.compile(r'class="searchtext"[^>]*>\s*<p>(.*?)</p>', re.I | re.S)
NOHITS_RE = re.compile(r'(No hits found|Nothing found)', re.I)
CLOUDFLARE_MARKS = ('Just a moment', 'cf-browser-verification', 'cf_chl_')


def strip_tags(text: str) -> str:
    """去掉 HTML 标签并反转义实体"""
    return html_module.unescape(HTML_TAG_RE.sub('', text or '')).strip()


def split_terms(text: str) -> List[str]:
    """按空格切分搜索词，保留引号内的整体（支持 female:big breasts 这类写法）"""
    terms, current, quoted = [], '', False
    for char in text or '':
        if char == '"':
            quoted = not quoted
            current += char
        elif char.isspace() and not quoted:
            if current.strip():
                terms.append(current.strip())
            current = ''
        else:
            current += char
    if current.strip():
        terms.append(current.strip())
    return terms


def build_query(keyword: str = '', includes: Sequence[str] = (),
                excludes: Sequence[str] = ()) -> str:
    """把关键词与标签拼成站点搜索语法（f_search）"""
    parts: List[str] = []
    if keyword.strip():
        parts.append(keyword.strip())
    for raw in includes:
        for term in split_terms(raw):
            # 已带引号或命名空间（如 language:chinese）时原样使用
            parts.append(term if ('"' in term or ':' in term) else f'"{term}"')
    for raw in excludes:
        for term in split_terms(raw):
            parts.append(f'-{term}' if ('"' in term or ':' in term) else f'-"{term}"')
    return ' '.join(parts)


def with_page(url: str, page: int) -> str:
    """在当前链接上替换/添加 page 参数"""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query['page'] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def build_search_url(keyword: str = '', includes: Sequence[str] = (),
                     excludes: Sequence[str] = (),
                     selected_mask: int = DEFAULT_SELECTED_MASK,
                     language: str = '', source: str = 'search',
                     page: int = 0) -> str:
    """构建列表页链接"""
    if source == 'front':
        return BASE_URL
    if source == 'popular':
        return POPULAR_URL if not page else f'{POPULAR_URL}?page={page}'

    query = build_query(keyword, includes, excludes)
    if language:
        query = (f'language:{language} ' + query).strip()
    params = {}
    if query:
        params['f_search'] = query
    mask = ALL_CATS_MASK & ~(selected_mask & ALL_CATS_MASK)
    if mask != DEFAULT_EXCLUDED_MASK:
        params['f_cats'] = str(mask)
    if page:
        params['page'] = str(page)
    return BASE_URL + ('?' + urlencode(params) if params else '')


@dataclass
class GalleryCard:
    """列表中的一个画廊条目"""
    url: str = ''
    title: str = ''
    category: str = ''
    tags: List[str] = field(default_factory=list)
    pages: str = ''
    uploader: str = ''
    rating: str = ''
    thumb: str = ''
    posted: str = ''

    @property
    def short_title(self) -> str:
        return self.title or self.url

    @property
    def meta_text(self) -> str:
        """列表里显示的一行摘要"""
        return ' · '.join(part for part in (
            self.category, self.pages,
            f'★{self.rating}' if self.rating else '',
            self.uploader, self.posted) if part)


@dataclass
class GalleryListPage:
    """一页列表结果"""
    url: str = ''
    cards: List[GalleryCard] = field(default_factory=list)
    prev_url: str = ''
    next_url: str = ''
    page_index: int = 0
    empty_reason: str = ''
    total_text: str = ''


class EHentaiBrowser:
    """画廊列表 / 搜索"""

    def __init__(self, cookie: str = '', timeout: int = 30, fetcher=None,
                 proxy: str = ''):
        self.fetcher = fetcher or HttpFetcher(cookie=cookie, timeout=timeout,
                                              proxy=proxy)
        self.cookie = self.fetcher.cookie

    # -- 抓取 ----------------------------------------------------------
    def search(self, url: str, page_index: int = 0) -> GalleryListPage:
        """抓取并解析列表页"""
        page_html = self.fetcher.text(url, referer=BASE_URL)
        if 'id="gdt"' not in page_html and \
                any(mark in page_html for mark in CLOUDFLARE_MARKS):
            raise RuntimeError('被站点的人机验证拦截了，请在'
                               '「文件 → 在线设置」里填写浏览器 Cookie')
        return self.parse_list(page_html, url, page_index)

    # -- 解析 ----------------------------------------------------------
    @staticmethod
    def parse_list(page_html: str, url: str, page_index: int = 0) -> GalleryListPage:
        page = GalleryListPage(url=url, page_index=page_index)
        for chunk in EHentaiBrowser._rows(page_html):
            card = EHentaiBrowser._parse_row(chunk, url)
            if card is not None and card.url:
                page.cards.append(card)
        page.total_text = EHentaiBrowser._result_text(page_html)
        if not page.cards:
            page.empty_reason = (page.total_text or
                                 ('没有找到结果' if NOHITS_RE.search(page_html) else
                                  '没有解析到内容（可能需要 Cookie，或站点页面结构有变化）'))
        page.prev_url, page.next_url = EHentaiBrowser._parse_pager(page_html, url)
        return page

    @staticmethod
    def _rows(page_html: str) -> List[str]:
        """切出画廊行：优先只在 itg 表格内切，兼容没有表格的旧结构"""
        block = page_html
        start = page_html.find('class="itg')
        if start != -1:
            block = page_html[start:]
            end = block.find('</table>')
            if end != -1:
                block = block[:end]
        return [chunk for chunk in block.split('<tr')[1:]
                if GALLERY_HREF_RE.search(chunk)]

    @staticmethod
    def _result_text(page_html: str) -> str:
        match = SEARCHTEXT_RE.search(page_html)
        if not match:
            return ''
        return strip_tags(match.group(1))

    @staticmethod
    def _parse_row(row: str, base_url: str) -> Optional[GalleryCard]:
        href = GALLERY_HREF_RE.search(row)
        if href is None:
            return None
        card = GalleryCard(url=urljoin(base_url, html_module.unescape(href.group(1))))
        card.category = strip_tags(EHentaiBrowser._div(row, 'category'))
        card.title = strip_tags(EHentaiBrowser._div(row, 'title'))
        card.tags = EHentaiBrowser._parse_tags(row)

        # 页数：新布局在 gl4c / glthumb 里写作「146 pages」，旧布局在 glcut
        pages = PAGES_RE.search(row)
        if pages:
            card.pages = f'{pages.group(1).replace(",", "")} 页'
        else:
            card.pages = strip_tags(EHentaiBrowser._div(row, 'cut'))

        uploader = UPLOADER_RE.search(row)
        if uploader:
            card.uploader = html_module.unescape(uploader.group(1))
        else:
            card.uploader = strip_tags(EHentaiBrowser._div(row, 'uploader'))

        posted = POSTED_RE.search(row)
        if posted:
            card.posted = html_module.unescape(posted.group(1)).strip()

        # 评分：部分布局把数值放在 ir 的 title 上（新布局由 JS 渲染，取不到就留空）
        rating = RATING_RE.search(row)
        if rating and rating.group(1).strip():
            card.rating = html_module.unescape(rating.group(1)).strip()

        image = IMG_RE.search(row)
        if image:
            source = DATA_SRC_RE.search(image.group(0)) or SRC_RE.search(image.group(0))
            if source:
                card.thumb = urljoin(base_url, html_module.unescape(source.group(1)))
        return card

    @staticmethod
    def _parse_tags(row: str) -> List[str]:
        """标签：新布局是多个 <div class="gt" title="namespace:tag">tag</div>，
        旧布局是一个 <div class="gt"> 里放若干 <a>"""
        tags: List[str] = []
        for inner in GT_DIV_RE.findall(row):
            if '<a' in inner.lower():
                tags.extend(text for text in
                            (strip_tags(anchor) for anchor in ANCHOR_RE.findall(inner))
                            if text)
            else:
                text = strip_tags(inner)
                if text:
                    tags.append(text)
        return tags

    @staticmethod
    def _div(row: str, key: str) -> str:
        match = DIV_RE[key].search(row)
        return match.group(1) if match else ''

    @staticmethod
    def _parse_pager(page_html: str, base_url: str) -> Tuple[str, str]:
        """上一页/下一页：先看锚点，再看 JS 变量，最后用 page 参数推算"""
        links: Dict[str, str] = {}
        for match in ANCHOR_TAG_RE.finditer(page_html):
            anchor = match.group(0)
            href = HREF_RE.search(anchor)
            if href and href.group(1) and href.group(1) != '#':
                links[match.group(1).lower()] = urljoin(
                    base_url, html_module.unescape(href.group(1)))
                continue
            number = LOAD_PAGE_RE.search(anchor)
            if number:
                links[match.group(1).lower()] = with_page(base_url, int(number.group(1)))
        for kind, target in JS_PAGER_RE.findall(page_html):
            if not target:
                continue
            key = 'dprev' if kind.lower() == 'prev' else 'dnext'
            if key not in links:
                links[key] = urljoin(base_url, html_module.unescape(target))
        return links.get('dprev', ''), links.get('dnext', '')


