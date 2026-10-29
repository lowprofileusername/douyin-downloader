# -*- coding: utf-8 -*-
"""抖音视频下载核心逻辑（无第三方 UI 依赖，仅需 playwright + chromium）。

抖音 web 端有 JS 风控（argus / jsvm / a_bogus 签名），纯 HTTP 与 yt-dlp 都拿不到播放地址，
必须用真实浏览器渲染并拦截 `aweme/v1/web/aweme/detail` 接口响应。
"""
import json
import os
import re
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REFERER = "https://www.douyin.com/"


def default_profile():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    if not base or base.startswith("~"):        # 环境变量缺失时别把 profile 落到 "~"
        base = tempfile.gettempdir()
    return os.path.join(base, "DouyinDownloader", "browser_profile")


def app_dir():
    """程序文件所在目录（打包后为 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def meipass_dir():
    """PyInstaller 数据目录：onefile 为临时解压目录，onedir 为 _internal。"""
    return getattr(sys, "_MEIPASS", None) or app_dir()


def _has_kernel(root):
    """判断某个 ms-playwright 风格目录下是否真有可用的 chromium 内核可执行文件。"""
    if not root or not os.path.isdir(root):
        return False
    try:
        names = os.listdir(root)
    except OSError:
        return False
    for n in names:
        if not (n.startswith("chromium") and os.path.isdir(os.path.join(root, n))):
            continue
        for sub in os.listdir(os.path.join(root, n)):
            exe = os.path.join(root, n, sub, "chrome-headless-shell.exe")
            if os.path.isfile(exe):
                return True
            exe2 = os.path.join(root, n, sub, "chrome.exe")
            if os.path.isfile(exe2):
                return True
    return False


def browser_dirs():
    """内核目录候选，按优先级排列：随附内核 → 用户目录。"""
    out = []
    env_set = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_set and env_set not in ("0", "1"):
        out.append(env_set)
    for base in (app_dir(), meipass_dir()):
        out.append(os.path.join(base, "browsers"))
        out.append(os.path.join(base, "_internal", "browsers"))
    out.append(os.path.join(app_dir(), "runtime", "browsers"))
    if sys.platform == "win32":
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.join(os.path.expanduser("~"), "AppData", "Local"))
    else:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    # 程序自己的内核位置（可选扩展包放这里 / 放 exe 旁边均可）
    out.append(os.path.join(base, "DouyinDownloader", "browsers"))
    out.append(os.path.join(base, "ms-playwright"))

    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return [p for p in uniq if _has_kernel(p)]


def ensure_browsers_path():
    """把 playwright 的内核搜索路径指到真实存在的目录。

    PyInstaller 冻结运行时，playwright 会把 PLAYWRIGHT_BROWSERS_PATH 置为 "0"，
    意为"到 exe 临时解压目录里找内核"，那里面没有内核，会报
    `Executable doesn't exist at ...\\_MEIxxxx\\playwright\\driver\\package\\.local-browsers\\...`。
    这里在启动驱动前显式指回真实位置（该变量用 setdefault 注入，故我们能覆盖它）。
    """
    dirs = browser_dirs()
    path = dirs[0] if dirs else os.path.join(app_dir(), "browsers")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = path
    return path


def find_system_browser():
    """查找系统已安装的 Edge / Chrome，作为随附内核缺失时的兜底。

    不能依赖 ProgramFiles / LOCALAPPDATA 环境变量：某些启动环境（沙箱、精简 shell、
    被清理过环境的进程）里这些变量可能整个缺失，届时明明装了 Edge 也检测不到。
    这里以注册表 App Paths 为准，再用常见安装路径兜底。
    """
    if sys.platform != "win32":
        return []
    found = {}

    try:
        import winreg
        access = winreg.KEY_READ
        try:
            access |= winreg.KEY_WOW64_64KEY      # 避免 32 位进程被重定向到 WOW6432Node
        except AttributeError:
            pass
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for channel, exe in (("msedge", "msedge.exe"), ("chrome", "chrome.exe")):
                if channel in found:
                    continue
                try:
                    with winreg.OpenKey(
                        root,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe,
                        0, access) as k:
                        val, _ = winreg.QueryValueEx(k, "")
                except OSError:
                    continue
                if val and os.path.isfile(val):
                    found[channel] = val
    except Exception:
        pass

    drive = (os.environ.get("SystemDrive") or "C:").rstrip("\\/") + os.sep
    user_local = (os.environ.get("LOCALAPPDATA")
                  or os.path.join(os.path.expanduser("~"), "AppData", "Local"))
    tails = {
        "msedge": [
            os.path.join(drive, "Program Files (x86)", "Microsoft", "Edge",
                         "Application", "msedge.exe"),
            os.path.join(drive, "Program Files", "Microsoft", "Edge",
                         "Application", "msedge.exe"),
        ],
        "chrome": [
            os.path.join(drive, "Program Files", "Google", "Chrome",
                         "Application", "chrome.exe"),
            os.path.join(drive, "Program Files (x86)", "Google", "Chrome",
                         "Application", "chrome.exe"),
            os.path.join(user_local, "Google", "Chrome", "Application", "chrome.exe"),
        ],
    }
    for channel, paths in tails.items():
        if channel in found:
            continue
        for p in paths:
            if os.path.isfile(p):
                found[channel] = p
                break

    return [(c, found[c]) for c in ("msedge", "chrome") if c in found]


def check_browsers_installed():
    """内核与系统浏览器都没有时，才给出明确错误。"""
    if browser_dirs() or find_system_browser():
        return
    raise RuntimeError(
        "没有可用的浏览器：既没检测到系统 Edge / Chrome，也没有内核包。\n"
        "解决方法（任选其一）：\n"
        "  1. 安装 Microsoft Edge 或 Google Chrome 后重试；\n"
        "  2. 把内核包解压出的 browsers 文件夹放到本程序旁边，即：\n"
        "     %s\n"
        "  3. 改用「完整版」（程序文件夹内自带内核）。"
        % os.path.join(app_dir(), "browsers"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_aweme_id(text):
    """从整段分享文本 / 短链 / 长链 / 纯 ID 中解析 aweme_id。"""
    s = (text or "").strip()

    m = re.search(r"douyin\.com/(?:video|share/video|note)/(\d+)", s)
    if m:
        return m.group(1)

    m = re.search(r"\b(\d{15,25})\b", s)
    if m and "http" not in s:
        return m.group(1)

    url = None
    for cand in re.findall(r"https?://[^\s\u4e00-\u9fff]+", s):
        if "douyin.com" in cand:
            url = cand
            break
    if not url:
        m = re.search(r"\b(\d{15,25})\b", s)
        if m:
            return m.group(1)
        raise ValueError("没找到抖音链接或作品 ID")

    opener = urllib.request.build_opener(_NoRedirect)
    location = url
    for _ in range(4):
        m = re.search(r"/(?:video|share/video|note)/(\d+)", location)
        if m:
            return m.group(1)
        m = re.search(r"modal_id=(\d+)", location)
        if m:
            return m.group(1)
        try:
            req = urllib.request.Request(location, headers={"User-Agent": UA})
            resp = opener.open(req, timeout=20)
            nxt = resp.headers.get("Location") or resp.geturl()
        except urllib.error.HTTPError as e:
            nxt = e.headers.get("Location") if e.headers else None
        except Exception as e:
            raise ValueError("链接解析失败: %s" % e)
        if not nxt or nxt == location:
            break
        location = nxt
    raise ValueError("未能从链接中提取作品 ID，请确认链接完整")


def launch_browser(p, profile, log=print):
    """启动浏览器：优先用随附内核，内核缺失时回退到系统 Edge / Chrome。"""
    ensure_browsers_path()
    common = dict(
        headless=True,
        viewport={"width": 1280, "height": 820},
        user_agent=UA,
        locale="zh-CN",
        args=["--disable-blink-features=AutomationControlled", "--mute-audio"],
    )
    has_bundled = bool(browser_dirs())
    plans = []
    if has_bundled:
        plans.append(("内置内核", {}, ""))
    for channel, path in find_system_browser():
        label = "系统 Edge" if channel == "msedge" else "系统 Chrome"
        plans.append((label, {"channel": channel}, path))

    errors = []
    for label, extra, path in plans:
        try:
            ctx = p.chromium.launch_persistent_context(profile, **common, **extra)
            if label != "内置内核":
                log("内置内核启动失败，已改用%s：%s" % (label, path) if has_bundled
                    else "未随附内核，本次使用%s：%s" % (label, path))
            return ctx
        except Exception as e:
            msg = str(e).strip().splitlines()
            errors.append("· %s：%s" % (label, (msg[0] if msg else repr(e))[:160]))

    raise RuntimeError(
        "无法启动浏览器（随附内核与系统浏览器均失败）：\n" + "\n".join(errors))


def capture_detail(aweme_id, profile, timeout=45, log=print):
    """用 Playwright 打开视频页，拦截 detail 接口响应。返回 aweme 详情 dict。"""
    check_browsers_installed()
    # 必须在 sync_playwright() 启动驱动进程**之前**设置：驱动一旦起来，
    # playwright 会用 setdefault 把该变量注入为 "0"（指向 exe 临时目录），之后改就无效了。
    ensure_browsers_path()
    from playwright.sync_api import sync_playwright

    url = "https://www.douyin.com/video/%s" % aweme_id
    captured = {}
    done = {"ok": False}
    seen = []

    with sync_playwright() as p:
        ctx = launch_browser(p, profile, log=log)
        page = ctx.new_page()

        def on_response(resp):
            try:
                u = resp.url
            except Exception:
                return
            if "/aweme/v1/" in u or "douyin.com/aweme/" in u:
                seen.append(u.split("?")[0])
            if "aweme/v1/web/aweme/detail" not in u:
                return
            try:
                body = resp.text()
            except Exception:
                return
            if "play_addr" not in body and "images" not in body:
                return
            try:
                data = json.loads(body)
            except Exception:
                return
            aw0 = data.get("aweme_detail")
            if isinstance(aw0, dict):
                captured["data"] = data
                done["ok"] = True
            elif data.get("aweme_list"):
                captured["data"] = data
                done["ok"] = True

        page.on("response", on_response)

        try:
            log("预热 douyin.com，获取基础 cookie")
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except Exception:
            pass

        for attempt in (1, 2):
            if done["ok"]:
                break
            log("打开视频页（第 %d 次尝试）" % attempt)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception:
                pass
            # 关键：必须用 wait_for_timeout 驱动事件循环，time.sleep 会让 on_response 永不触发
            deadline = time.time() + timeout
            while not done["ok"] and time.time() < deadline:
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    break
                if not done["ok"]:
                    try:
                        page.mouse.wheel(0, 300)
                    except Exception:
                        pass
        ctx.close()

    if not captured.get("data"):
        if seen:
            log("已捕获接口（诊断）：" + ", ".join(sorted(set(seen))[:4]))
            raise RuntimeError(
                "拿到了接口响应但不含播放地址。通常是被登录墙拦截，"
                "请到抖音网页版登录后重试（程序会复用登录状态）。"
            )
        raise RuntimeError(
            "页面未发起任何接口请求，大概率命中验证码或网络不通。请稍后重试。"
        )

    raw = captured["data"]
    return raw.get("aweme_detail") or (raw.get("aweme_list") or [{}])[0]


def list_qualities(aw):
    """列出可用画质档位，按像素数、码率降序。"""
    v = aw.get("video") or {}
    out = []
    for i, br in enumerate(v.get("bit_rate") or []):
        addr = br.get("play_addr") or {}
        urls = [u for u in (addr.get("url_list") or []) if "aweme/v1/play" not in u]
        if not urls:
            continue
        w, h = addr.get("width") or 0, addr.get("height") or 0
        out.append({
            "gear": br.get("gear_name") or ("#%d" % i),
            "bitrate": br.get("bit_rate") or 0,
            "width": w,
            "height": h,
            # 抖音档位按短边标定：1920x1080 是 1080p，720x1280 是 720p
            "short": min(w, h) if w and h else 0,
            "h265": bool(br.get("is_h265")),
            "url": urls[0],
        })
    if not out:
        for key in ("play_addr_h264", "play_addr"):
            node = v.get(key)
            if isinstance(node, dict):
                urls = [u for u in (node.get("url_list") or []) if "aweme/v1/play" not in u]
                if urls:
                    w = node.get("width") or 0
                    h = node.get("height") or 0
                    out.append({
                        "gear": key, "bitrate": 0, "width": w, "height": h,
                        "short": min(w, h) if w and h else 0,
                        "h265": "265" in key, "url": urls[0],
                    })
                    break
    out.sort(key=lambda c: (c["width"] * c["height"], c["bitrate"],
                            0 if c["h265"] else 1), reverse=True)
    return out


def pick_quality(cands, max_height):
    """挑短边不高于 max_height 的最高档；没有则返回最高档并给出实际画质。

    档位按短边判断（竖屏 720x1280 属 720p）。抖音标称 540p 的实际短边是 576，
    故留 10% 容差，否则选 540P 会一路降级到更低档。
    """
    if not cands:
        return None, None
    auto = max_height >= 99999
    # 容差仅用于消化抖音的标称取整（标称 540p 实际短边 576）
    fit = [c for c in cands if 0 < c["short"] <= (10 ** 9 if auto else max_height * 1.05)]
    if fit:
        best = fit[0]                      # 目标档位之内取最高
    else:
        # 所有档位都高于目标时，取最接近目标的一档，而不是直接跳到最高画质
        best = min(cands, key=lambda c: c["short"] or 10 ** 9)
    # 明显低于目标档位时返回实际画质，由界面如实提示"源片最高仅 XP"
    warn = None if auto or best["short"] >= max_height * 0.95 else best["short"]
    return best, warn


def download_file(url, path, on_progress=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": REFERER, "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9", "Range": "bytes=0-",
    })
    with urllib.request.urlopen(req, timeout=180) as r:
        try:
            total = int(r.headers.get("Content-Length") or 0)
        except Exception:
            total = 0
        done = 0
        with open(path, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    return os.path.getsize(path)


def is_valid_mp4(path):
    if os.path.getsize(path) < 50_000:
        return False
    with open(path, "rb") as f:
        return b"ftyp" in f.read(32)


def probe_mp4(path):
    """纯 Python 解析 MP4：返回时长、分辨率、是否含音轨。"""
    info = {"duration": 0.0, "width": 0, "height": 0, "has_audio": False}

    def boxes(f, start, end, want):
        out, pos = [], start
        while pos + 8 <= end:
            f.seek(pos)
            head = f.read(8)
            if len(head) < 8:
                break
            size, typ = struct.unpack(">I4s", head)
            typ = typ.decode("latin-1")
            hdr = 8
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
                hdr = 16
            elif size == 0:
                size = end - pos
            if size < hdr:
                break
            if typ in want:
                out.append((typ, pos + hdr, pos + size))
            pos += size
        return out

    with open(path, "rb") as f:
        size = os.path.getsize(path)
        for _, ms, me in boxes(f, 0, size, {"moov"}):
            for _, bs, _be in boxes(f, ms, me, {"mvhd"}):
                # payload 起始处即 version(1)+flags(3)，其后才是时间戳字段
                f.seek(bs)
                ver = f.read(1)[0]
                if ver == 0:
                    # creation(4) + modification(4) + timescale(4) + duration(4)
                    f.seek(bs + 4 + 8)
                    ts = struct.unpack(">I", f.read(4))[0]
                    dur = struct.unpack(">I", f.read(4))[0]
                else:
                    # creation(8) + modification(8) + timescale(4) + duration(8)
                    f.seek(bs + 4 + 16)
                    ts = struct.unpack(">I", f.read(4))[0]
                    dur = struct.unpack(">Q", f.read(8))[0]
                info["duration"] = dur / ts if ts else 0.0
            for _, ts_, te in boxes(f, ms, me, {"trak"}):
                handler, w, h = "?", 0, 0
                for typ, bs, be in boxes(f, ts_, te, {"mdia", "tkhd"}):
                    if typ == "tkhd":
                        f.seek(bs)
                        ver = f.read(1)[0]
                        off = bs + (4 + 4 + 4 + 4 + 4 + 4 + 4 + 4 + 2 + 2 + 2 + 2 + 36
                                    if ver == 0 else
                                    4 + 8 + 8 + 8 + 8 + 8 + 8 + 2 + 2 + 2 + 2 + 36)
                        f.seek(off)
                        data = f.read(8)
                        if len(data) == 8:
                            w = struct.unpack(">I", data[:4])[0] >> 16
                            h = struct.unpack(">I", data[4:])[0] >> 16
                    else:
                        for _t3, hs, _he in boxes(f, bs, be, {"hdlr"}):
                            f.seek(hs + 8)
                            handler = f.read(4).decode("latin-1", "replace")
                if handler == "soun":
                    info["has_audio"] = True
                elif handler == "vide":
                    info["width"], info["height"] = w, h
    return info


def safe_name(s, n=40):
    s = re.sub(r"[\\/:*?\"<>|\r\n\t#]", "", (s or "").strip())
    return re.sub(r"\s+", " ", s)[:n]
