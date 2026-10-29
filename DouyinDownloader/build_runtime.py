# -*- coding: utf-8 -*-
"""生成随附浏览器内核目录 runtime/browsers。

默认形态（--onedir 单 exe）不需要它：程序会直接调用系统 Edge / Chrome。
只有在目标机器既没有 Edge 也没有 Chrome 时，才需要带上这个内核目录，
把生成的 browsers/ 放在 exe 旁边即可，程序会自动识别。

用法：
    python -m playwright install chromium      # 先确保本机有内核
    python build_runtime.py                    # 生成 runtime/browsers
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "runtime", "browsers")

# 只保留中英文语言包，其余 40 多个国家语言包共约 40MB，无谓占用
KEEP_LOCALES = ("en-US.pak", "zh-CN.pak", "zh-TW.pak")


def source_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    root = os.path.join(base, "ms-playwright")
    if not os.path.isdir(root):
        return None
    hits = [n for n in os.listdir(root)
            if n.startswith("chromium_headless_shell-")
            and os.path.isdir(os.path.join(root, n))]
    if not hits:
        return None
    hits.sort()
    d = os.path.join(root, hits[-1])
    for sub in os.listdir(d):
        exe = os.path.join(d, sub, "chrome-headless-shell.exe")
        if os.path.isfile(exe):
            return d, d, exe
    return None


def main():
    found = source_dir()
    if not found:
        print("未找到本机内核，请先执行：python -m playwright install chromium")
        return 1
    root, src, exe = found
    print("来源:", src)

    if os.path.isdir(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST)

    name = os.path.basename(src)
    target = os.path.join(DEST, name)
    shutil.copytree(src, target)
    for marker in ("INSTALLATION_COMPLETE", "DEPENDENCIES_VALIDATED"):
        p = os.path.join(root, marker)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(DEST, marker))

    # 裁剪：删文档、只留中英文语言包
    removed = 0
    for junk in ("debug.log", "ABOUT", "LICENSE.headless_shell"):
        p = os.path.join(target, junk)
        if os.path.isfile(p):
            os.remove(p)
            removed += 1
    loc = os.path.join(target, "chrome-headless-shell-win64", "locales")
    if os.path.isdir(loc):
        for f in os.listdir(loc):
            if f not in KEEP_LOCALES:
                os.remove(os.path.join(loc, f))
                removed += 1

    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(DEST) for f in fs)
    print("已清理 %d 个文件，内核目录 %.0f MB -> %s" % (removed, total / 1048576, DEST))
    print("用法：把 %s 整个目录拷贝到 exe 旁边" % name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
