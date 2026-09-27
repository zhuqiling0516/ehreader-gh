# ehreader（安卓版漫画阅读器）

Kivy 写的安卓界面，数据层复用桌面版的 `pages.py`（HTTP/代理/Cookie/画廊解析）与
`browse.py`（列表与搜索）。仓库按「GitHub Actions 云端打包」的结构排好：

```
.
├── .github/workflows/android-build.yml   # 云端构建，产物 ehreader-apk
├── mobile/                               # Kivy 应用
│   ├── main.py  mobui.py  ehapi.py  selftest.py
│   ├── buildozer.spec                    # 已开 android.accept_sdk_license = True
│   ├── build_apk.sh / build_apk.ps1 / preview.bat
│   └── ci/android-build.yml              # workflow 的原始副本
├── pages.py                              # 与桌面版共用（构建时复制到 mobile/vendor/）
└── browse.py                             # 同上
```

## 拿 APK（本机不用装任何安卓环境）

1. 推到 GitHub 的 `main`（或 `master`）分支；
2. 打开仓库 **Actions → Build Android APK**，push 会自动触发，也可点 **Run workflow** 手动跑；
3. 首次构建要下 Android SDK/NDK（约 5~6 GB），大概 30~60 分钟；之后有缓存会快很多；
4. 构建完成后在该次运行页面底部 **Artifacts** 下载 `ehreader-apk`（zip 里是
   `ehreader-1.0.0-debug.apk`，debug 版可直接装）。

## 本地预览 / 自测（不需要安卓）

```powershell
py -3.13 -m pip install kivy requests pillow
cd mobile
py -3.13 main.py                 # 预览界面
py -3.13 -X utf8 selftest.py ui  # 自测（本地模拟站点）
```

## 说明

- `python-for-android` 只能在 Linux 上跑，Windows 原生不行，所以走 Actions（或 WSL2/Docker）。
- release 签名（可选）：`keytool -genkey ...` 生成 keystore 后按 `mobile/README.md` 里的
  注释往 `buildozer.spec` 里加 `android.release_keystore` 等四项，再把 workflow 的
  `buildozer -v android debug` 改成 `release`。
