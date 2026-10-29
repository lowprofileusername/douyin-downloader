# DouyinDownloader

抖音视频下载器，Windows 桌面程序。粘贴分享链接或作品 ID，选择画质，下载带音轨的 MP4。

## 特性

- **单个 exe，双击即用**：49 MB，无需安装 Python 或任何依赖
- **不自带浏览器内核**：直接调用系统已有的 Edge / Chrome（都是 Chromium），因此体积能控制在 49 MB 而非 300 MB+
- **画质可选**：最高画质（自动）/ 1080P / 720P / 540P。源片没有更高档位时会如实提示实际拿到的画质，不会静默降级
- **高分屏适配**：按系统 DPI 缩放（200% 缩放下字号、内边距、窗口等比放大）
- **右键菜单**：链接框、目录框、日志区均支持剪切/复制/粘贴/删除/全选
- **粘贴自动清洗**：直接粘贴抖音整段分享文字（含换行、表情、日期）也能正确提取链接

## 下载到的画质说明

程序取的是抖音提供的**无水印最高档位**，但上限由作者上传的源片决定：
作者传的是 1080P 横屏就能拿到 1920×1080，源片本身是 720P 竖屏则最高只有 720×1280。
没有更高档位时，界面会明确显示实际画质，不会假装成功。

## 使用方法

1. 在抖音点「分享 → 复制链接」，得到一段带链接的分享文字
2. 粘贴到程序顶部输入框（或直接填 19 位作品 ID）
3. 选保存目录和画质，点「下载」

## 从源码运行

```bash
pip install playwright
python -m playwright install chromium    # 可选，见下方内核说明
python DouyinDownloader/app.py
```

## 打包

```bash
pyinstaller --noconsole --onefile --name DouyinDownloader --icon icon.ico app.py
```

### 浏览器内核策略

程序按以下顺序找可用浏览器，**都不依赖环境变量**（读注册表 `App Paths`，
再用常见安装路径兜底 —— 某些启动环境下 `ProgramFiles` 等变量会是空的）：

1. exe 旁边的 `browsers/` 目录（可选，一般不需要）
2. 系统 Edge（注册表查找）
3. 系统 Chrome（注册表查找）

只有目标机器既没有 Edge 也没有 Chrome（如 Windows Server 精简版）时，
才需要生成并附带内核：

```bash
python DouyinDownloader/build_runtime.py     # 生成 runtime/browsers（约 234 MB）
```

把生成的 `browsers/` 整个目录放在 exe 旁边即可，程序会自动使用。

## 已知限制

- 使用系统级 DPI 感知，非逐显示器感知。Tk 不处理 `WM_DPICHANGED`，
  把窗口拖到缩放比例不同的副屏时不会重新排版
- 依赖抖音 web 端接口，若抖音改版导致接口变化，需要更新抓取逻辑

## 文件结构

```
DouyinDownloader/
  app.py            界面（Tk，高分屏适配）
  core.py           抓取、选档、下载、MP4 校验
  make_icon.py      生成程序图标（纯标准库）
  build_runtime.py  生成可选随附内核目录
  icon.ico
douyin_1080p.py     命令行版下载脚本
```
