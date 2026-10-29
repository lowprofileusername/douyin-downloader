# -*- coding: utf-8 -*-
"""从清华镜像下载 wheel 到 wheelhouse/，供离线 pip 安装使用。

本机 pip 直连官方源会 IncompleteRead、对清华源又报 "from versions: none"，
但 curl 访问清华源正常，故改为 curl 取索引+抓文件，再 --no-index 本地安装。

用法: python fetch_wheel.py <包名> [文件特征串, 如 cp312 / win_amd64]
"""
import os
import re
import subprocess
import sys
import urllib.request

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
WH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wheelhouse")


def fetch_index(pkg):
    url = "%s/%s/" % (MIRROR, pkg)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def latest_url(html, feat):
    hrefs = [h for h in re.findall(r'href="([^"]+)"', html)
             if ".whl" in h and all(f in h for f in feat)]
    if not hrefs:
        return None

    def ver(h):
        name = h.split("/")[-1].split("#")[0]
        m = re.search(r"-([0-9][^-]*)-", name)
        return tuple(int(p) for p in re.findall(r"\d+", m.group(1))) if m else (0,)
    hrefs.sort(key=ver)
    return hrefs[-1].split("#")[0]


def download(pkg, rel):
    url = "%s/%s" % (MIRROR, rel.lstrip("/").replace("../../", "", 1))
    url = re.sub(r"^https://pypi\.tuna\.tsinghua\.edu\.cn/simple/packages/",
                 "https://pypi.tuna.tsinghua.edu.cn/packages/", url)
    os.makedirs(WH, exist_ok=True)
    out = os.path.join(WH, rel.split("/")[-1])
    subprocess.run(["curl", "-sL", "--retry", "5", "--retry-all-errors",
                    "-m", "300", url, "-o", out], check=True)
    return out


if __name__ == "__main__":
    pkg = sys.argv[1]
    feat = sys.argv[2:] or ["py3-none-any"]
    html = fetch_index(pkg)
    rel = latest_url(html, feat)
    if not rel:
        sys.exit("未找到匹配 wheel: %s %s" % (pkg, feat))
    path = download(pkg, rel)
    print("%-28s %.1f MB" % (os.path.basename(path), os.path.getsize(path) / 1048576))
