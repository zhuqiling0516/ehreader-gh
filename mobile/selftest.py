# -*- coding: utf-8 -*-
"""移动端自测：模拟 e-hentai 站点，验证数据层与（可选）Kivy 界面

    py -3.13 selftest.py            # 只测数据层（无窗口）
    py -3.13 selftest.py ui         # 额外启动 Kivy 界面并驱动一遍
                                    # 注意：不要用 --ui，Kivy 会把自己的命令行参数解析走

用本地 HTTP 服务器模拟站点结构（列表 + 画廊 + 观看页 + 图片 + 封面），
不访问真实网站。
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
for _candidate in (BASE_DIR.parent, BASE_DIR):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

import browse  # noqa: E402
from ehapi import Library, Store, data_dir  # noqa: E402

PORT = 8791
BASE = f'http://127.0.0.1:{PORT}'
PAGES = 6
RESULTS = []


def check(label: str, ok, detail: str = '') -> None:
    RESULTS.append((label, bool(ok), detail))
    print(f'[{"OK " if ok else "FAIL"}] {label}' + (f'  -> {detail}' if detail else ''))


IMAGES = {}
for n in range(1, PAGES + 1):
    buffer = io.BytesIO()
    Image.new('RGB', (300 + 10 * n, 420 + 12 * n), (25 * n, 70, 110)).save(buffer, 'PNG')
    IMAGES[n] = buffer.getvalue()

LIST_HTML = f'''<html><body><div class="searchtext"><p>Found 2 results. </p></div>
<table class="itg gltc"><tr><th>Title</th></tr>
<tr><td class="gl1c glcat"><div class="cn cta">Manga</div></td>
<td class="gl2c"><div class="glthumb"><img src="{BASE}/thumb/1.jpg"></div>
<div><div class="ir"></div><div>6 pages</div></div></td>
<td class="gl3c glname"><a href="{BASE}/g/3001/mobiletok/"><div class="glink">移动端测试漫画 A</div>
<div><div class="gt" title="language:chinese">chinese</div>
<div class="gt" title="female:big breasts">big breasts</div></div></a></td>
<td class="gl4c glhide"><a href="{BASE}/uploader/mobile">mobile</a>
<div id="posted_3001">2026-09-27 12:00</div></td></tr>
<tr><td class="gl1c glcat"><div class="cn cta">Doujinshi</div></td>
<td class="gl2c"><div class="glthumb"><img src="{BASE}/thumb/2.jpg"></div>
<div><div class="ir"></div><div>4 pages</div></div></td>
<td class="gl3c glname"><a href="{BASE}/g/3002/mobiletok2/"><div class="glink">移动端测试漫画 B</div>
<div><div class="gt" title="language:japanese">japanese</div></div></a></td>
<td class="gl4c glhide"><a href="{BASE}/uploader/mobile">mobile</a>
<div id="posted_3002">2026-09-27 11:00</div></td></tr>
</table><script>var prevurl="";var nexturl="{BASE}/?next=1";</script></body></html>'''

GALLERY_HTML = f'''<html><body><h1 id="gj">移动端画廊</h1><div id="gdt">
{''.join(f'<a href="{BASE}/s/aaaabbbb{n:02d}/4000-{n}"><div>x</div></a>' for n in range(1, PAGES + 1))}
</div></body></html>'''
VIEWER_HTML = f'<html><body><img id="img" src="{BASE}/img/{{n}}.png"></body></html>'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        kind, body = 'text/html; charset=utf-8', b''
        if path.startswith('/thumb/'):
            buffer = io.BytesIO()
            Image.new('RGB', (140, 200), (190, 120, 60)).save(buffer, 'JPEG')
            kind, body = 'image/jpeg', buffer.getvalue()
        elif path.startswith('/img/'):
            index = int(path.rsplit('/', 1)[-1].split('.')[0])
            kind, body = 'image/png', IMAGES.get(index, b'')
        elif path.startswith('/s/'):
            body = VIEWER_HTML.format(n=int(path.rsplit('-', 1)[1])).encode('utf-8')
        elif path.startswith('/g/'):
            body = GALLERY_HTML.encode('utf-8')
        else:
            body = LIST_HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def wait(callback, timeout=10.0):
    """等后台线程回调（数据层测试用）；单参数回调会自动拆包"""
    box = {}

    def collect(*args):
        box.setdefault('result', args[0] if len(args) == 1 else args)

    callback(collect)
    deadline = time.time() + timeout
    while 'result' not in box and time.time() < deadline:
        time.sleep(0.05)
    return box.get('result')


def run_logic_tests(tmp: Path) -> None:
    browse.BASE_URL = f'{BASE}/'
    browse.POPULAR_URL = f'{BASE}/popular'
    store = Store(tmp / 'store.json')
    library = Library(store)
    store.set_setting('proxy', 'direct')          # 本机 mock，直连

    url = library.search_url(keyword='test', tags='big breasts', exclude='yaoi',
                             language='chinese')
    page = wait(lambda cb: library.search_async(url, cb, lambda e: cb(None, e)))
    check('搜索能解析列表', page is not None and len(page.cards) == 2,
          f'cards={len(page.cards) if page else 0}')
    if not page or not page.cards:
        return
    card = page.cards[0]
    check('列表字段完整（分类/页数/上传者/标签）',
          card.category == 'Manga' and card.pages == '6 页' and card.uploader == 'mobile'
          and card.tags[:2] == ['chinese', 'big breasts'],
          f'{card.category} / {card.pages} / {card.uploader} / {card.tags}')
    check('分页链接解析', bool(page.next_url) and not page.prev_url,
          page.next_url[-20:])
    check('结果总数解析', 'Found 2 results' in page.total_text, page.total_text)

    check('收藏漫画', store.toggle_favorite(card) and store.is_favorite(card.url),
          f'{len(store.favorite_cards())} 部')
    check('再次切换=取消收藏', not store.toggle_favorite(card) and
          not store.is_favorite(card.url))
    store.toggle_favorite(card)
    check('收藏漫画的标签', store.add_tags(card.tags) == 2 and
          store.tags() == card.tags, str(store.tags()))
    check('标签自动去重', store.add_tags(card.tags) == 0)
    check('删除单个标签', store.remove_tag('chinese') and store.tags() == ['big breasts'])
    store.clear_tags()
    check('清空标签', store.tags() == [] and store.add_tags(['chinese']) == 1)

    opened = wait(lambda cb: library.open_async(card.url, cb, lambda e: cb([], e)))
    check('打开在线画廊', opened and len(opened[0]) == PAGES and
          opened[1] == '移动端画廊', f'{len(opened[0])} 页 / {opened[1]}')
    if opened and opened[0]:
        pages = opened[0]
        image = pages[0].fetch((300, 300, 'contain', 1.0))
        check('下载并解码原图', image.width > 0 and image.height > 0,
              f'{image.size} {image.format}')
        store.save_progress(card.url, 3, len(pages), 'single')
        record = store.progress_of(card.url)
        check('阅读进度写入/读出', record.get('index') == 3 and
              record.get('total') == PAGES, str(record))
        store.push_recent(card.url, '移动端画廊')
        check('最近打开记录', store.recent()[0]['title'] == '移动端画廊')

    manga = tmp / '本地漫画'
    manga.mkdir()
    for n in range(1, 5):
        Image.new('RGB', (200 + n, 300 + n), (30 * n, 60, 90)).save(manga / f'{n:02d}.png')
    check('扫描本地目录', len(library.scan_folder(manga)) == 4,
          f'{len(library.scan_folder(manga))} 张')
    store.set_setting('local_dirs', [str(tmp)])
    check('本地目录候选列表', any(item == manga for item in library.local_choices()))
    local = wait(lambda cb: library.open_async(str(manga), cb, lambda e: cb([], e)))
    check('打开本地漫画', local and len(local[0]) == 4 and local[1] == '本地漫画',
          f'{len(local[0])} 页 / {local[1]}')

    store.save()
    again = Store(tmp / 'store.json')
    check('配置与收藏持久化', again.proxy() == 'direct' and
          bool(again.favorite_cards()) and again.tags() == ['chinese'],
          f'proxy={again.proxy()} 收藏={len(again.favorite_cards())} 标签={again.tags()}')
    check('数据目录可用', data_dir().is_dir(), str(data_dir()))


def run_ui_tests(tmp: Path) -> None:
    """构建真实界面并手动驱动 Clock.tick() 验证

    说明：这里不调用 App.run()，而是自己 build() + Clock.tick()。
    本机环境（Windows + SDL2，Python 3.13）下，复杂控件树进入 App.run()
    的主循环后 Clock 不再推进（进程空闲、无异常），因此改用 Kivy 官方
    测试推荐的 Clock.tick() 手动推进方式，行为等价且更可控。
    安卓上正常使用 App.run()（见 main()）。
    """
    import time

    from kivy.base import EventLoop
    from kivy.clock import Clock
    from kivy.core.window import Window

    import main as mobile_main

    progress = BASE_DIR / 'ui_progress.log'

    def mark(text: str) -> None:
        try:
            with progress.open('a', encoding='utf-8') as handle:
                handle.write(text + '\n')
        except Exception:
            pass

    try:
        progress.unlink()
    except Exception:
        pass

    EventLoop.ensure_window()
    mark('window ok')
    app = mobile_main.MobileApp()
    app.store_path = tmp / 'ui_store.json'
    root = app.build()
    Window.add_widget(root)
    mark('build ok')

    def spin(seconds: float) -> None:
        """手动推进事件循环（等价于主循环跑 seconds 秒）"""
        end = time.time() + seconds
        while time.time() < end:
            Clock.tick()
            time.sleep(0.02)

    try:
        spin(0.6)
        check('UI：三个页面都建好', app.browse is not None and app.reader is not None
              and app.settings is not None and app.sm.current == 'browse')

        app.browse.keyword.text = 'test'
        app.browse.do_search()
        spin(4.0)
        check('UI：搜索出结果', len(app.browse.cards) == 2,
              f'cards={len(app.browse.cards)}')
        if app.browse.rows:
            row = app.browse.rows[0]
            check('UI：列表行渲染（标题/元信息）',
                  '测试漫画 A' in row.card.title and row.card.pages == '6 页',
                  f'{row.card.title} / {row.card.pages}')
            check('UI：封面异步加载成功', row.cover.texture is not None)

        app.browse.select_row(app.browse.rows[0])
        check('UI：单击选中行', app.browse.selected is not None and
              app.browse.fav_button.text == '☆收藏', app.browse.fav_button.text)
        app.browse.toggle_selected_favorite()
        spin(0.2)
        check('UI：收藏成功并出现★', app.browse.rows[0].star.text == '★' and
              len(app.store.favorite_cards()) == 1)
        app.browse.favorite_selected_tags()
        check('UI：收藏该漫画的标签',
              app.store.tags() == ['chinese', 'big breasts'], str(app.store.tags()))

        app.browse.switch_view('favorites')
        spin(0.5)
        check('UI：切到收藏夹视图', len(app.browse.cards) == 1 and
              app.browse.view == 'favorites')
        check('UI：收藏夹内禁用翻页', app.browse.prev_button.disabled and
              app.browse.next_button.disabled)

        app.browse.open_card(app.browse.cards[0])
        spin(4.0)
        check('UI：进入阅读器并解析页面', len(app.reader.pages) == PAGES,
              f'{len(app.reader.pages)} 页')
        check('UI：单页视图已创建并加载图片',
              app.reader._page_view is not None and
              app.reader._page_view.image.texture is not None)

        start = app.reader.index
        app.reader.next_page()
        check('UI：下一页', app.reader.index == start + 1, f'index={app.reader.index}')
        app.reader.prev_page()
        check('UI：上一页', app.reader.index == start, f'index={app.reader.index}')
        app.reader.tap_page(app.reader.width - 5)
        after_right = app.reader.index
        app.reader.tap_page(5)
        check('UI：点击左/右区域翻页（方向感知）', after_right != app.reader.index,
              f'右={after_right} 左={app.reader.index}')
        app.reader.goto(2)
        check('UI：跳页', app.reader.index == 2)
        check('UI：阅读进度已写入 store',
              app.store.progress_of(app.reader.source).get('index') == 2)

        app.reader.toggle_mode()
        spin(1.5)
        check('UI：切到连页模式', app.reader.mode == 'scroll' and
              app.reader._scroll is not None)
        loaded = sum(1 for image in app.reader._scroll_images.values()
                     if image.texture is not None)
        check('UI：连页模式按需加载了图片', loaded > 0, f'已加载 {loaded} 页')
        app.reader.toggle_mode()
        check('UI：切回单页模式', app.reader.mode == 'single' and
              app.reader._page_view is not None)

        app.show_settings()
        app.settings.proxy_input.text = 'direct'
        app.settings.save()
        check('UI：设置页保存生效', app.store.proxy() == 'direct')
        app.show_browse()
        check('UI：返回浏览页', app.sm.current == 'browse')
        check('UI：整体流程无异常', True)
    except Exception as exc:
        import traceback
        check('UI：执行异常', False, f'{type(exc).__name__}: {exc}')
        mark('TRACEBACK:\n' + traceback.format_exc()[-2000:])
    mark('done')



def write_report(path: Path, extra: str = '') -> None:
    """把结果写到文件（PowerShell 重定向会打乱中文输出，自己写更可靠）"""
    lines = [f'# 移动端自测报告  {time.strftime("%Y-%m-%d %H:%M:%S")}', extra]
    for label, ok, detail in RESULTS:
        lines.append(f'[{"OK " if ok else "FAIL"}] {label}' + (f'  -> {detail}' if detail else ''))
    failed = [item for item in RESULTS if not item[1]]
    lines.append(f'\n共 {len(RESULTS)} 项，失败 {len(failed)} 项')
    for label, _ok, detail in failed:
        lines.append(f'  x {label} {detail}')
    try:
        path.write_text('\n'.join(lines), encoding='utf-8')
    except Exception:
        pass


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix='mobile_selftest_'))
    server = start_server()
    extra = ''
    try:
        print('=== 数据层自测 ===')
        run_logic_tests(tmp)
        if 'ui' in sys.argv:
            print('=== Kivy 界面自测 ===')
            extra = '（含 Kivy 界面自测）'
            run_ui_tests(tmp)
    except Exception as exc:
        check('自测过程异常', False, f'{type(exc).__name__}: {exc}')
    finally:
        server.shutdown()
        write_report(BASE_DIR / 'selftest_report.txt', extra)
        shutil.rmtree(tmp, ignore_errors=True)
        for leftover in (BASE_DIR / 'mobile_store.json',):
            if leftover.exists():
                leftover.unlink()
    failed = [item for item in RESULTS if not item[1]]
    print(f'\n共 {len(RESULTS)} 项，失败 {len(failed)} 项')
    for label, _ok, detail in failed:
        print(f'  x {label} {detail}')
    sys.exit(1 if failed else 0)



if __name__ == '__main__':
    main()


