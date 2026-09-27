[app]

title = 漫画阅读器 EH
package.name = ehreader
package.domain = org.ehreader

# 打包 mobile/ 目录本身；pages.py / browse.py 由 build_apk 脚本复制到 vendor/
source.dir = .
source.include_exts = py,kv,json,ini,png,jpg,atlas,ttf
source.exclude_dirs = __pycache__, .buildozer, bin, vendor_old
source.exclude_patterns = selftest_report.txt, ui_progress.log, *_test.py, _*probe*.py

version = 1.0.0

# python-for-android 需要的依赖（requests 用于网络，pillow 负责解码 webp 等）
requirements = python3,kivy==2.3.1,requests,urllib3,idna,charset-normalizer,certifi,pillow

orientation = all
fullscreen = 1
# 安卓返回键交给 Kivy 处理（代码里用 key == 27 判断）

# 手机上网读图，必须联网权限；读本地漫画还需要存储权限
android.permissions = INTERNET,ACCESS_NETWORK_STATE,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
# 安卓 11+ 若要读取任意目录的本地漫画，可额外加上（上架商店时需说明用途）：
# android.permissions = INTERNET,ACCESS_NETWORK_STATE,MANAGE_EXTERNAL_STORAGE

# CI（无人值守）里必须自动接受 SDK 许可，否则构建会卡在 y/N 交互上
android.accept_sdk_license = True

android.api = 34
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True
android.release_artifact = apk

p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 1
