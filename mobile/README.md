# 手机版漫画阅读器（安卓 UI，Kivy）

给 e-hentai 工具加的**安卓界面**：在线阅读/搜索/标签筛选/收藏/标签收藏/本地阅读。
界面用 Kivy 写成，数据层直接复用桌面版已经验证过的 `pages.py`（HTTP/代理/Cookie/画廊解析）
和 `browse.py`（列表与搜索），所以桌面与手机的行为一致。

```
mobile/
├── main.py            界面：浏览页 / 阅读器页 / 设置页（ScreenManager）
├── mobui.py           通用控件：异步取图（Pillow 解码 webp）、列表行、提示条
├── ehapi.py           数据层：配置/收藏/标签/进度 JSON 存储 + Library（搜索/打开/本地目录）
├── selftest.py        自测：本地模拟站点 + 数据层与界面检查（不需要真实网站）
├── buildozer.spec     打包配置（APK 名字、权限、依赖）
├── build_apk.sh       Linux/WSL/Docker 里一条命令打 APK
├── build_apk.ps1      Windows 上准备构建 / 自测 / 调 Docker
├── preview.bat        Windows 双击预览界面
├── ci/android-build.yml   GitHub Actions 云端构建（不需要本机 Android 环境）
└── vendor/            共享模块的工作副本（构建脚本自动生成，已被 .gitignore 忽略）
```

## 一、先在电脑上预览界面（不用安卓也能看）

双击 `preview.bat`，或者：

```powershell
py -3.13 -m pip install kivy requests pillow   # 已经装过就跳过
cd mobile
py -3.13 main.py
```

窗口就是手机界面的放大版：上面搜索框、下面列表、点一行选中、双击进阅读器。

## 二、跑自测（会起一个本地模拟站点，不访问真实网站）

```powershell
cd mobile
py -3.13 -X utf8 selftest.py          # 只测数据层（无窗口，19 项）
py -3.13 -X utf8 selftest.py ui       # 追加界面自测（22 项）
```

结果同时写入 `selftest_report.txt`。

> 注意：参数要用 `ui` 而不是 `--ui`，因为 Kivy 会自己解析 `--` 开头的命令行参数。

## 三、打包 APK

python-for-android **只能在 Linux 上跑**，Windows 原生不行。三条路线任选：

### 路线 A：WSL2 / Linux（推荐）

```bash
wsl --install -d Ubuntu                       # Windows 上装一次
# 以下在 Ubuntu 里执行
sudo apt update && sudo apt install -y git zip unzip openjdk-17-jdk python3-pip \
     autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev \
     libtinfo6 cmake libffi-dev libssl-dev
pip install --user buildozer cython
cd /mnt/d/<你的路径>/e-hentai/mobile
./build_apk.sh              # 首次会下载 Android SDK/NDK（约 3~6 GB），比较久
```

产物：`mobile/bin/ehreader-1.0.0-debug.apk`（debug 版可直接装）。

### 路线 B：Docker

```powershell
cd mobile
.\build_apk.ps1 -Docker
# 等价于：
docker run --rm -v "${PWD}:/home/user/hostcwd" kivy/buildozer android debug
```

### 路线 C：GitHub Actions（本机什么都不用装）

把 `ci/android-build.yml` 复制成仓库的 `.github/workflows/android-build.yml`，
push 后在 Actions 里 Run workflow，构建完成在 Artifacts 下载 `ehreader-apk`。

### release 签名（可选）

```bash
keytool -genkey -v -keystore eh.keystore -alias eh -keyalg RSA -keysize 2048 -validity 10000
# 然后 buildozer.spec 里加：
#   android.release_keystore = eh.keystore
#   android.release_keyalias = eh
#   android.release_keystore_passwd = 你的密码
#   android.release_keyalias_passwd = 你的密码
buildozer android release
```

### 安装到手机

```bash
adb install -r mobile/bin/*-debug.apk
```

或把 APK 拷到手机点击安装（需要在系统设置里允许「安装未知来源应用」）。

## 四、安卓上怎么用

| 位置 | 说明 |
| --- | --- |
| 顶部搜索框 | 关键词 / e-hentai 搜索语法，例如 `language:chinese parodies:xxx` |
| 筛选 | 包含标签、排除标签、语言、来源（全部/首页/热门）、分类勾选框 |
| 结果列表 | 单击选中，**双击直接进阅读器**；★ 表示已收藏 |
| 底部按钮 | 上一页/下一页、阅读、☆收藏、收藏标签 |
| 收藏夹 / 最近打开 | 顶部分段按钮切换；收藏夹里在线漫画和本地漫画都在 |
| 标签收藏 | 弹出已收藏标签，点一下加入搜索并立即搜；可单独删除 |
| 本地 | 扫描设置里配置的目录（如 `/sdcard/Download`），列出可直接阅读的漫画 |

阅读器：

- **单页**：点屏幕左/右区域翻页（默认从右到左，可在设置里改）；左右滑动也可以；
  **双击**放大 2 倍、再双击还原；放大后可拖动看图。
- **连页**：上下滚动一条长图，只加载视野附近两屏（省流量、省内存）。
- 顶栏：`←` 返回、页码、☆收藏、单页/连页切换、**目录**（按页号跳）。
- 每本书的阅读进度、阅读方向、缩放方式都会记住（存在应用私有目录的
  `mobile_store.json`）。

设置页：代理、Cookie、本地目录、阅读方向、缩放方式、清空收藏/标签/阅读记录，
以及「测试连接」（能明确告诉你是代理问题、人机验证还是网络不通）。

### 代理与 Cookie

- 代理留空 = 跟随系统/VPN；填 `direct` = 强制直连；也可以填
  `http://127.0.0.1:7890`、`socks5://127.0.0.1:1080`。
  注意：SOCKS 代理需要 `PySocks`（桌面自带，安卓上没装会提示；此时改用 HTTP 代理）。
- 出现 509 / 需要登录时，把浏览器里的 Cookie 整条粘到设置页即可（会随每次请求发送）。

## 五、和桌面版的关系

| 模块 | 桌面版 | 手机版 |
| --- | --- | --- |
| 网络/站点解析 | `pages.py` | **同一个文件**（打包时复制到 `vendor/`） |
| 列表/搜索 | `browse.py` | **同一个文件** |
| 界面 | `reader.py` / `ui.py`（tkinter） | `main.py` + `mobui.py`（Kivy） |
| 存储 | `reader_progress.json` | `mobile_store.json`（互不影响） |

## 六、已知限制

- 连页模式下页面高度要等图片解码后才知道，所以滚动条长度会随加载变化（不影响使用）。
- 手机内存有限，单页模式最多缓存 28 张纹理（`mobui.TEXTURE_LIMIT`），超出按最久未用淘汰。
- Android 11+ 想读任意目录的本地漫画，需要在 `buildozer.spec` 里换成
  `MANAGE_EXTERNAL_STORAGE` 权限（已写好注释），上架商店需说明用途。
- 站点若弹人机验证，需要在设置里填 Cookie。
