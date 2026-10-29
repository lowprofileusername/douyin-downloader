# -*- coding: utf-8 -*-
"""在 Skill 拿到 detail JSON 的基础上，挑选真正的 1080P 源下载并校验。

只用本文件，不改动已安装的 Skill：
  python douyin_1080p.py <短链|长链|aweme_id> -o <输出目录>
"""
import argparse
import importlib.util
import json
import os
import sys

SKILL_SCRIPT = os.path.join(
    os.path.expanduser("~"), ".workbuddy", "skills",
    "kris-douyin-video-downloader", "scripts", "douyin_download.py",
)


def load_skill():
    spec = importlib.util.spec_from_file_location("dy_skill", SKILL_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_tracks(path):
    """纯 Python 解析 MP4，返回 [(handler, width, height)]。"""
    import struct

    def boxes(f, start, end, want):
        out = []
        pos = start
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

    size = os.path.getsize(path)
    tracks = []
    with open(path, "rb") as f:
        for _t, moov_s, moov_e in boxes(f, 0, size, {"moov"}):
            for _t2, trak_s, trak_e in boxes(f, moov_s, moov_e, {"trak"}):
                handler, w, h = "?", 0, 0
                for typ, bs, be in boxes(f, trak_s, trak_e, {"mdia", "tkhd"}):
                    if typ == "tkhd":
                        f.seek(bs)
                        ver = f.read(1)[0]
                        # version0: 4+4+4+4+4+4+4+4+2+2+2+2+36 then 8+8 w/h
                        off = bs + (4 + 4 + 4 + 4 + 4 + 4 + 4 + 4 + 2 + 2 + 2 + 2 + 36
                                    if ver == 0 else
                                    4 + 8 + 8 + 8 + 8 + 8 + 8 + 2 + 2 + 2 + 2 + 36)
                        f.seek(off)
                        data = f.read(8)
                        if len(data) == 8:
                            w = struct.unpack(">I", data[:4])[0] >> 16
                            h = struct.unpack(">I", data[4:])[0] >> 16
                    else:  # mdia
                        for typ3, hs, he in boxes(f, bs, be, {"hdlr"}):
                            f.seek(hs + 8)
                            handler = f.read(4).decode("latin-1", "replace")
                tracks.append({"handler": handler, "width": w, "height": h})
    return tracks


def pick_1080p(aw):
    """从 bit_rate 列表中挑最高画质源，优先 1080p + H.264。"""
    v = aw.get("video") or {}
    cands = []
    for i, br in enumerate(v.get("bit_rate") or []):
        addr = br.get("play_addr") or {}
        urls = [u for u in (addr.get("url_list") or []) if "aweme/v1/play" not in u]
        if not urls:
            continue
        cands.append({
            "gear": br.get("gear_name") or f"#{i}",
            "bitrate": br.get("bit_rate") or 0,
            "w": addr.get("width") or 0,
            "h": addr.get("height") or 0,
            "codec": br.get("is_h265") and "h265" or "h264",
            "quality": br.get("quality_type"),
            "url": urls[0],
        })
    # 兜底：bit_rate 为空时用 play_addr_h264
    if not cands:
        for k in ("play_addr_h264", "play_addr"):
            node = v.get(k)
            if isinstance(node, dict):
                urls = [u for u in node.get("url_list", []) if "aweme/v1/play" not in u]
                if urls:
                    cands.append({
                        "gear": k, "bitrate": 0,
                        "w": node.get("width") or 0, "h": node.get("height") or 0,
                        "codec": "h264" if "264" in k else "?", "quality": None,
                        "url": urls[0],
                    })
                    break
    # 排序：像素数优先，其次码率，H.264 在同规格下优先（兼容性最好）
    cands.sort(key=lambda c: (c["w"] * c["h"], c["bitrate"],
                              1 if c["codec"] == "h264" else 0), reverse=True)
    return cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("-o", "--out", default="./downloads")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    dy = load_skill()
    os.makedirs(args.out, exist_ok=True)
    sync_playwright = dy.load_playwright()
    aweme_id = dy.resolve_aweme_id(args.source)
    print(f"[+] aweme_id = {aweme_id}", flush=True)

    raw = dy.capture_detail(sync_playwright, aweme_id, dy.DEFAULT_PROFILE, args.timeout)
    aw = raw.get("aweme_detail") or (raw.get("aweme_list") or [{}])[0]

    author = dy.safe_name((aw.get("author") or {}).get("nickname"), 24) or "douyin"
    desc = dy.safe_name(aw.get("desc"), 40)
    base = f"{author}_{desc}_{aweme_id}".strip("_")
    json_path = os.path.join(args.out, f"{base}_detail.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"[+] 详情 JSON: {json_path}", flush=True)

    v = aw.get("video") or {}
    print(f"[+] 作者 {author} | 时长 {(v.get('duration') or 0)/1000:.1f}s "
          f"| 标注 {v.get('width')}x{v.get('height')} ratio={v.get('ratio')}", flush=True)

    cands = pick_1080p(aw)
    print(f"[+] 候选源 {len(cands)} 个：", flush=True)
    for c in cands:
        print(f"    - {c['gear']:<16} {c['w']}x{c['h']} "
              f"{c['bitrate']/1000:.0f}kbps {c['codec']}", flush=True)

    for c in cands:
        path = os.path.join(args.out, f"{base}.mp4")
        print(f"[↓] 尝试 {c['gear']} ({c['w']}x{c['h']}) ...", flush=True)
        try:
            size, ct = dy.download(c["url"], path)
        except Exception as e:
            print(f"    [!] 失败 {e}", flush=True)
            continue
        if not dy.is_valid_mp4(path):
            print(f"    [!] 无效文件 {size} bytes {ct}", flush=True)
            os.path.exists(path) and os.remove(path)
            continue
        tracks = parse_tracks(path)
        print(f"    [✓] {size/1024/1024:.2f} MB  轨道: {tracks}", flush=True)
        print(json.dumps({
            "path": path, "size": size, "gear": c["gear"],
            "declared": f"{c['w']}x{c['h']}", "codec": c["codec"],
            "tracks": tracks, "desc": aw.get("desc"), "author": author,
        }, ensure_ascii=False), flush=True)
        return
    raise SystemExit("[x] 全部候选失败")


if __name__ == "__main__":
    main()
