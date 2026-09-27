#!/usr/bin/env bash
# 在 Linux / WSL2 / Docker 里构建安卓 APK
#   用法：  bash build_apk.sh            # 打 debug APK（可直接装）
#           bash build_apk.sh release    # 打 release（需要签名，见 README）
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 1/4 收集共享模块到 vendor/"
mkdir -p vendor
cp -f ../pages.py ../browse.py vendor/
ls -1 vendor/

echo "==> 2/4 语法检查"
python3 -m py_compile ehapi.py mobui.py main.py selftest.py

echo "==> 3/4 检查工具链"
if ! command -v buildozer >/dev/null 2>&1; then
    echo "未找到 buildozer，先安装："
    echo "  pip install --user buildozer cython"
    echo "  （还需要：openjdk-17-jdk autoconf libtool pkg-config zlib1g-dev"
    echo "    libncurses5-dev libncursesw5-dev libtinfo6 cmake libffi-dev libssl-dev）"
    exit 1
fi
if ! command -v java >/dev/null 2>&1; then
    echo "未找到 java，请安装 OpenJDK 17（Ubuntu: sudo apt install openjdk-17-jdk）"
    exit 1
fi

echo "==> 4/4 buildozer android ${1:-debug}"
buildozer -v "android" "${1:-debug}"

echo
echo "完成，APK 位置："
ls -lh bin/*.apk 2>/dev/null || echo "  （bin/ 下没找到 apk，请看上面 buildozer 的输出）"
