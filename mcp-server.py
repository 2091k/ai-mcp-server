#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP管理器 Flet 版（mcpgateway 替换 supergateway）2.3 深色优化版
（由 tkinter 版迁移而来，功能与操作保持一致）

功能说明：
1. 串行安装：网关 → MCP服务包依次安装，全部完成后自动启动服务
2. 安装流程全程锁定启动按钮，整套流程（安装+启动）结束才释放
3. 深色主题界面；全部可调参数集中在文件顶部，方便修改
4. 服务器列表高度可拖动分隔条手动调节；调试日志面板随窗口自动缩放
5. 日志自动滚动到底部（用户向上翻阅时不打扰，回到底部后恢复自动滚底）
6. 日志可上下滑动查看；限制最大行数，长时间运行不卡顿、不暴涨内存
7. 窗口关闭拦截：有服务器运行中会弹窗提示并先停止进程再退出，避免端口被占用
8. 环境变量 MCP_HEADLESS=1 时以隐藏窗口启动（用于无人值守/调试）
9. 安装依赖时，调试输出面板顶部按程序逐行显示实时进度：百分比 + 真实下载包体大小 + 已下载依赖包数(x/y)，安装结束自动隐藏；npm 内部 http 日志(cache/fetch)不刷调试窗口
10. 启动入口解析升级：优先读包内 package.json 的 bin 字段（支持任意 bin 名，不再依赖文件名含"mcp"），失败自动退回 .bin 目录扫描；可手动指定启动命令/JS入口
11. 启动加速：用普通类替换 Flet 0.25 巨慢的图标 Enum（实测 import flet 从 13 秒降到 1.5 秒），窗口秒开；工具栏新增"清除日志"按钮

运行依赖：Python 3.8+，Flet 0.25+
    pip install flet
"""
import json
import os
import subprocess
import threading
import queue
import sys
import signal
import re
import time
import socket
import atexit
import traceback
from collections import deque
from typing import List, Optional, Tuple

try:
    import ctypes
except ImportError:  # 非 Windows 平台
    ctypes = None

import importlib.util  # 用于定位 flet 包路径（快速图标加载用）
import types           # 用于构建快速版 flet.core.icons 模块

_START_TIME = time.time()  # 启动计时（含 Flet 导入与客户端启动）


def _install_fast_icons_stub() -> bool:
    """用普通类替换 flet.core.icons 的巨型 Enum，解决 Flet 0.25 导入极慢问题。

    Flet 0.25 把约 1.8 万个 Material 图标定义为 Python Enum，光执行该枚举就
    需 10~25 秒（本机实测 13 秒），是"启动后等好几秒才出窗口"的元凶。
    已全面排查：flet 内部对 Icons 只做属性访问（无构造/索引/遍历等 Enum 专属
    操作），可安全替换为普通类属性（import flet 降到约 1.5 秒）。
    任何失败都静默回退到原版导入，功能不受影响。
    """
    try:
        spec = importlib.util.find_spec("flet")
        if spec is None or not spec.origin:
            return False
        icons_py = os.path.join(os.path.dirname(spec.origin), "core", "icons.py")
        if not os.path.isfile(icons_py):
            return False
        src = open(icons_py, "r", encoding="utf-8").read()
        # 提取类体内 名称 = "值" 成员（原文件为 Enum 成员，类体内缩进4空格）
        members = re.findall(r'^\s{4}([A-Z][A-Z0-9_]*)\s*=\s*"([^"]*)"\s*$', src, re.M)
        if not members:
            return False
        lines = ["import random", ""]
        for cls in ("Icons", "icons"):
            lines.append(f"class {cls}:")
            lines.append(f"    _all_names = None")
            for name, val in members:
                lines.append(f"    {name} = {val!r}")
            lines.append("")
            lines.append("    @staticmethod")
            lines.append("    def random():")
            lines.append(f"        if {cls}._all_names is None:")
            lines.append(f"            {cls}._all_names = [n for n in dir({cls}) if n.isupper()]")
            lines.append(f"        return getattr({cls}, random.choice({cls}._all_names))")
            lines.append("")
        mod = types.ModuleType("flet.core.icons")
        mod.__file__ = icons_py
        sys.modules["flet.core.icons"] = mod
        exec(compile("\n".join(lines), icons_py, "exec"), mod.__dict__)
        return True
    except Exception:
        sys.modules.pop("flet.core.icons", None)
        return False


_FAST_ICONS_OK = _install_fast_icons_stub()

import flet as ft

# =====================================================================
#                        界面参数（可自由调整）
# ---------------------------------------------------------------------
# 修改本区块任意数值后保存，重新运行即可生效，无需改动其它代码。
# =====================================================================

# ---------- 窗口大小 ----------
MAIN_WINDOW_WIDTH = 600          # 窗口初始宽度（像素）
MAIN_WINDOW_HEIGHT = 880         # 窗口初始高度（像素）
MAIN_WINDOW_MIN_WIDTH = 620      # 窗口最小宽度
MAIN_WINDOW_MIN_HEIGHT = 520     # 窗口最小高度

# ---------- 服务器列表高度（可拖动中间分隔条手动调整） ----------
SERVER_LIST_INIT_HEIGHT = 165    # 服务器列表初始高度（像素）
SERVER_LIST_MIN_HEIGHT = 120     # 拖动分隔条时列表最小高度
SERVER_LIST_MAX_HEIGHT = 520     # 拖动分隔条时列表最大高度

# ---------- 分隔条 ----------
SPLITTER_HEIGHT = 8              # 分隔条厚度（像素）
SPLITTER_ICON_SIZE = 16          # 分隔条拖动图标大小

# ---------- 配色（深色主题，可自由改色） ----------
COLOR_BG             = "#16181d"  # 窗口整体背景
COLOR_PANEL          = "#1e2229"  # 面板背景（服务器列表 / 日志标题区）
COLOR_PANEL_BORDER   = "#323842"  # 面板边框
COLOR_LOG_BG         = "#0d1117"  # 调试日志区背景
COLOR_LOG_FG         = "#00e676"  # 调试日志文字颜色（绿色）
COLOR_TEXT           = "#e6edf3"  # 普通文字颜色
COLOR_TEXT_DIM       = "#8b949e"  # 次要文字颜色（表头 / 提示）
COLOR_SPLITTER       = "#323842"  # 分隔条颜色

COLOR_BTN_ADD     = "#2ea043"  # "＋添加"按钮
COLOR_BTN_START   = "#238636"  # "启动"按钮
COLOR_BTN_STOP    = "#da3633"  # "停止"按钮
COLOR_BTN_EDIT    = "#b88700"  # "编辑"按钮
COLOR_BTN_DELETE  = "#da3633"  # "删除"按钮
COLOR_BTN_TEXT    = "#ffffff"  # 按钮文字颜色

# ---------- 字体 ----------
FONT_FAMILY       = "Microsoft YaHei UI"  # 界面默认字体（中文）
LOG_FONT_FAMILY   = "Consolas"            # 日志区等宽字体（英文数字更整齐）

# ---------- 各区域文字大小 ----------
FONT_SIZE_TITLE         = 18   # 顶部大标题"MCP 服务器"
FONT_SIZE_TOOLBAR       = 14   # 工具条 / 面板标题文字
FONT_SIZE_HEADER        = 12   # 服务器列表表头（名称/端口/状态/操作）
FONT_SIZE_LIST          = 13   # 服务器列表每行内容
FONT_SIZE_BUTTON        = 13   # 所有按钮文字
FONT_SIZE_LOG           = 15   # 调试日志文字
FONT_SIZE_DIALOG        = 13   # 弹窗正文文字
FONT_SIZE_DIALOG_TITLE  = 16   # 弹窗标题文字

# ---------- 列表列宽（与行内容保持一致，改这里即可） ----------
COL_NAME_WIDTH   = 130   # "名称"列宽
COL_PORT_WIDTH   = 64    # "端口"列宽
COL_STATUS_WIDTH = 92    # "状态"列宽
BTN_WIDTH        = 72    # 每个操作按钮宽度（启动/编辑/删除一致）
BTN_HEIGHT       = 36    # 每个操作按钮高度
ROW_SPACING      = 8     # 行内控件间距

# ---------- 日志 ----------
MAX_LOG_LINES        = 6000   # 日志最大行数，超出自动丢弃最旧行（防内存暴涨）
LOG_FLUSH_INTERVAL   = 0.05   # 日志刷新间隔（秒），批量合并，避免高频刷新卡顿
LOG_STICK_BOTTOM     = True   # 是否自动滚动日志到底部（True=自动，False=不自动）
LOG_SCROLL_TOLERANCE = 40.0   # 判断"回到底部"的容差（像素），越小越严格

# ---------- 其它行为 ----------
CHECK_PORT_ON_START       = True   # 启动时检查服务器端口是否被占用并提示（防残留进程占端口）
CONFIRM_CLOSE_WITH_RUNNING = True  # 有服务器运行时关闭窗口是否先弹确认框
CLOSE_GUARD_DELAY          = 0.5   # 关闭拦截延迟生效秒数（避开 Flet 0.25 首帧构建 bug，建议 0.3~1.0）

# ---------- 安装进度条（显示在调试输出面板顶部） ----------
PROGRESS_BAR_HEIGHT = 6            # 进度条高度（像素）
PROGRESS_BAR_COLOR  = "#2ea043"    # 进度条前景色（绿色）
PROGRESS_BAR_TRACK  = "#323842"    # 进度条轨道颜色
PROGRESS_HIDE_DELAY = 1.5          # 安装完成/失败后进度条停留秒数，再自动隐藏

# =====================================================================
#                      （以下一般无需修改）
# =====================================================================

DEFAULT_CONFIG = {
    "mcpServers": {
        "demo-mcp": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"]
        }
    }
}

log_queue = queue.Queue()
progress_queue = queue.Queue()  # 安装进度条消息队列（显示/更新/隐藏）


def _hide_progress_after(seconds: float):
    """延迟向进度队列发送隐藏消息：安装结束（成功/失败）后进度条短暂停留再自动隐藏"""
    threading.Timer(max(0.0, float(seconds)), lambda: progress_queue.put({"type": "hide"})).start()


# 基础目录：源码运行时=脚本所在目录；冻结(exe)时=exe 所在目录
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.abspath(os.path.dirname(sys.argv[0]))
GATEWAY_DIR = os.path.join(BASE_DIR, "gateway")
# 配置文件：固定放在 BASE_DIR（源码=脚本目录 / exe=exe所在目录），
# 避免受启动目录影响导致找不到配置
CONFIG_FILE = os.path.join(BASE_DIR, "mcp_servers.json")



# =====================================================================
# 进程登记与退出兜底清理
# ---------------------------------------------------------------------
# 无论窗口以何种方式关闭（点 ✕、Alt+F4、异常退出等），只要 Python 进程
# 退出，atexit 都会强制结束所有登记在册的子进程（含进程树），保证不会
# 残留进程占用端口、导致下次无法启动。
# =====================================================================
_proc_registry_lock = threading.Lock()
_proc_registry = {}  # pid -> 服务器名


def register_proc(pid: int, name: str):
    """登记已启动的子进程，用于退出前兜底清理"""
    if not pid:
        return
    with _proc_registry_lock:
        _proc_registry[pid] = name


def unregister_proc(pid: int):
    """进程已正常结束后移出登记表"""
    if not pid:
        return
    with _proc_registry_lock:
        _proc_registry.pop(pid, None)


def kill_all_registered_procs():
    """强制结束所有登记在册的子进程（含子进程树），退出前兜底清理用"""
    with _proc_registry_lock:
        pids = list(_proc_registry.keys())
    for pid in pids:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    check=False,
                    timeout=5,
                    creationflags=_no_window_flags(),
                )
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


atexit.register(kill_all_registered_procs)

# 收到终止信号时兜底清理子进程（防止进程残留占用端口）
try:
    def _signal_cleanup(signum, frame):
        try:
            kill_all_registered_procs()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_cleanup)
    signal.signal(signal.SIGINT, _signal_cleanup)
except Exception:
    pass

if sys.platform == "win32":
    NPM_CMD = "npm.cmd"
    SCRIPT_EXT = ".cmd"
else:
    NPM_CMD = "npm"
    SCRIPT_EXT = ""


def _no_window_flags() -> int:
    """Windows 下让子进程在后台运行、不弹 cmd 窗口。

    GUI 子系统 exe（--windowed）派生控制台子进程（npm.cmd / mcpgateway.cmd /
    taskkill 等）时，Windows 默认会新建一个可见的 cmd 窗口；用户一旦关掉该窗口，
    整个进程树会被终止。加 CREATE_NO_WINDOW 标志即可彻底避免弹窗。
    非 Windows 平台返回 0（无此概念）。
    """
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0

# 隐藏控制台窗口（仅 Windows 有效；无控制台时自动跳过，不影响运行）
if sys.platform == "win32" and ctypes is not None:
    try:
        time.sleep(0.05)
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def strip_pkg_version(pkg_full: str) -> str:
    if pkg_full.startswith("@"):
        scope, namever = pkg_full.split("/", 1)
        if "@" in namever:
            name = namever.split("@")[0]
            return f"{scope}/{name}"
        return pkg_full
    else:
        if "@" in pkg_full:
            return pkg_full.split("@")[0]
        return pkg_full


def get_server_work_dir(server_name: str) -> str:
    return os.path.join(BASE_DIR, server_name)


def get_gateway_bin() -> str:
    return os.path.join(GATEWAY_DIR, "node_modules", ".bin", f"mcpgateway{SCRIPT_EXT}")


def scan_mcp_cmd_in_bin(bin_dir: str, log_callback) -> Optional[str]:
    if not os.path.isdir(bin_dir):
        log_callback(f"[扫描] 目录不存在：{bin_dir}")
        return None

    candidates = []
    try:
        for fname in os.listdir(bin_dir):
            if fname.lower().endswith(".cmd") and "mcp" in fname.lower():
                fullpath = os.path.join(bin_dir, fname)
                candidates.append(fullpath)
    except Exception as e:
        log_callback(f"[扫描] 读取目录失败：{str(e)}")
        return None

    if not candidates:
        log_callback("[扫描] .bin目录未找到名称包含mcp的cmd文件")
        return None

    log_callback(f"[扫描] 找到候选cmd文件：{candidates[0]}")
    return candidates[0]


def extract_js_from_npm_cmd(cmd_file_path: str, log_callback) -> Optional[str]:
    log_callback(f"[解析] 准备读取cmd文件路径：{cmd_file_path}")
    if not os.path.exists(cmd_file_path):
        log_callback("[解析] 文件不存在！")
        return None

    try:
        with open(cmd_file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        log_callback(f"[解析] 读取cmd失败: {str(e)}")
        return None

    bin_dir = os.path.dirname(cmd_file_path)
    pat1 = re.compile(r'"%dp0%([^"]+\.(js|njs))"')
    match = pat1.search(content)
    if match:
        raw_rel = match.group(1)
        rel_js = raw_rel.lstrip('\\/')
        temp_path = os.path.join(bin_dir, rel_js)
        abs_js = os.path.abspath(temp_path)
        if os.path.isfile(abs_js):
            log_callback(f"[解析] ✅ JS入口有效：{abs_js}")
            return abs_js

    pat2 = re.compile(r'["\s]([^\s"]+\.(js|njs))["\s]')
    candidates = pat2.findall(content)
    for rel, ext in candidates:
        if rel.startswith('%dp0%'):
            continue
        rel_clean = rel.lstrip('\\/')
        temp_path = os.path.join(bin_dir, rel_clean)
        abs_js = os.path.abspath(temp_path)
        if os.path.isfile(abs_js):
            log_callback(f"[解析] 备用正则匹配到JS入口：{abs_js}")
            return abs_js

    log_callback("[解析] ❌ 未能从cmd脚本找到有效JS入口")
    return None


def resolve_npx_entry(work_dir: str, pkg_name: str, log_callback=None) -> Optional[str]:
    """方案3 主解析：读包内 package.json 的 bin 字段确定启动入口（权威、无需猜文件名）。

    返回 JS 入口绝对路径；失败返回 None（调用方可退回扫描 .bin 目录兜底）。
    log_callback 可省略（用于安装前"是否已安装"的静默检查）。
    """
    def _log(msg: str):
        if log_callback is not None:
            log_callback(msg)

    try:
        clean = strip_pkg_version(pkg_name)  # 去掉 @latest / @1.2.3，保留 @scope/name
        pkg_dir = os.path.join(work_dir, "node_modules", *clean.split("/"))
        pkg_json = os.path.join(pkg_dir, "package.json")
        if not os.path.isfile(pkg_json):
            _log(f"[解析] 未找到包描述文件：{pkg_json}")
            return None
        with open(pkg_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        bin_field = data.get("bin")
        if not bin_field:
            _log(f"[解析] {clean} 的 package.json 未声明 bin 字段")
            return None
        if isinstance(bin_field, str):
            rel = bin_field
        elif isinstance(bin_field, dict):
            short = clean.split("/")[-1].lower()  # @scope/name -> name
            if short in bin_field:
                rel = bin_field[short]
            else:
                # 多 bin 包：取第一个（与 npx 默认行为一致的近似）
                rel = next(iter(bin_field.values()))
                _log(f"[解析] 多bin包 {clean}，选取：{list(bin_field.keys())[0]}")
        else:
            return None
        entry_js = os.path.abspath(os.path.join(pkg_dir, rel))
        if not os.path.isfile(entry_js):
            _log(f"[解析] bin 指向的文件不存在：{entry_js}")
            return None
        _log(f"[解析] ✅ 从 package.json bin 定位到 JS 入口：{entry_js}")
        return entry_js
    except Exception as e:
        _log(f"[解析] 读取 package.json 失败: {e}")
        return None


# =====================================================================
# 安装进度辅助：包体大小查询 + 真实百分比进度
# （dry-run 预解析拿总依赖数 -> 正式安装数 npm http fetch 行 -> 已下载 x/y）
# =====================================================================
def _progress_set(row_id: str, label: str, value, pct: Optional[str] = None):
    """更新某一行进度（value=None 表示不确定进度 -> 动画效果）"""
    progress_queue.put({"type": "set", "id": row_id, "label": label, "value": value, "pct": pct})


def format_size(num_bytes) -> str:
    """字节数 -> 人类可读大小（如 2.1 MB / 30 KB）"""
    try:
        b = float(num_bytes)
        if b >= 1024 ** 3:
            return f"{b / 1024 ** 3:.2f} GB"
        if b >= 1024 ** 2:
            return f"{b / 1024 ** 2:.1f} MB"
        if b >= 1024:
            return f"{b / 1024:.0f} KB"
        return f"{int(b)} B"
    except Exception:
        return ""


def query_package_size(pkg_name: str) -> Optional[int]:
    """查询 npm registry 获取该包 tarball 的真实下载大小（字节）；失败返回 None（不显示）"""
    tarball_url = _registry_tarball_url(pkg_name)
    if not tarball_url:
        return None
    return _tarball_size_range(tarball_url)


def _registry_tarball_url(pkg_name: str) -> Optional[str]:
    """获取该包最新版 dist.tarball 下载地址"""
    try:
        import urllib.request
        url = "https://registry.npmjs.org/" + pkg_name.replace("/", "%2F")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 mcp-manager/2.4"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # registry 根文档中 dist 位于 versions[latest] 内
        latest = (data.get("dist-tags") or {}).get("latest")
        if latest:
            dist = (data.get("versions") or {}).get(latest, {}).get("dist") or {}
            tb = dist.get("tarball")
            if tb:
                return tb
    except Exception:
        pass
    # 兜底：npm view dist.tarball（走 npm 自身网络配置，兼容代理等环境）
    try:
        proc = subprocess.Popen(
            [NPM_CMD, "view", pkg_name, "dist.tarball", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window_flags(),
        )
        out, _ = proc.communicate(timeout=30)
        if proc.returncode == 0:
            tb = out.strip().strip('"')
            if tb.startswith("http"):
                return tb
    except Exception:
        pass
    return None


def _tarball_size_range(tarball_url: str) -> Optional[int]:
    """Range 请求只取 1 字节，从 Content-Range 解析 tarball 真实下载总大小"""
    try:
        import urllib.request
        req = urllib.request.Request(tarball_url, headers={
            "User-Agent": "Mozilla/5.0 mcp-manager/2.4",
            "Range": "bytes=0-0",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            cr = resp.headers.get("Content-Range") or ""
            # 格式: bytes 0-0/1184467
            total = cr.rsplit("/", 1)[-1].strip()
            if total.isdigit():
                return int(total)
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                return int(cl)
    except Exception:
        pass
    return None


def _npm_dry_run_total(pkg_name: str, work_dir: str) -> Optional[int]:
    """dry-run 预解析（不下载）：返回本次安装将添加的依赖包总数；失败返回 None"""
    try:
        cmd = [NPM_CMD, "install", "--dry-run", "--json", "--no-audit", "--no-fund",
               "-y", pkg_name, "--prefix", work_dir]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=BASE_DIR,
            creationflags=_no_window_flags(),
        )
    except FileNotFoundError:
        raise
    except Exception:
        return None
    try:
        out, _ = proc.communicate(timeout=180)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return None
    if proc.returncode != 0:
        return None
    try:
        # npm 10 会在 JSON 前打印 "add xxx 1.0.0" 行，需从第一个 "{" 开始解析
        data = json.loads(out[out.find("{"):])
        added = data.get("added")
        return int(added) if added is not None else None
    except Exception:
        return None


def _npm_install_progress(pkg_name: str, work_dir: str, row_id: str,
                          row_title: str, log_callback) -> bool:
    """带真实百分比进度的 npm install：
    1) dry-run 预解析拿到总依赖数 total（失败则退回动画进度，百分比显示 --）
    2) 正式安装加 --loglevel=http，逐行统计 npm http fetch 输出 -> 已下载 x/total
    3) 出现 "added N packages" 即视为完成（100%）
    找不到 npm 时抛出 FileNotFoundError，由调用方统一处理。
    """
    total = _npm_dry_run_total(pkg_name, work_dir)
    if total:
        _progress_set(row_id, f"{row_title} ｜ 解析完成，共 {total} 个依赖包，开始下载", None)
        log_callback(f"[进度] {pkg_name} 依赖解析完成，预计 {total} 个依赖包")
    else:
        _progress_set(row_id, f"{row_title} ｜ 正在下载依赖包 ...", None)

    fetched = 0        # 已下载包数
    last_pct = -1      # 上次推送的百分比（百分比不变时不重复刷）
    done_flag = {"done": False}

    def read_pipe(pipe):
        nonlocal fetched, last_pct
        for line in iter(pipe.readline, ""):
            s = line.rstrip()
            if not s.strip():
                continue
            if "http fetch" in s and "GET" in s:
                # 下载行：只用于统计进度，不刷调试日志（避免刷屏）
                fetched += 1
                if total:
                    pct = min(fetched / total, 1.0)
                    pct_int = int(round(pct * 100))
                    if pct_int != last_pct:
                        last_pct = pct_int
                        _progress_set(row_id,
                                      f"{row_title} ｜ 下载 {fetched}/{total} 个依赖包",
                                      pct, f"{pct_int}%")
                else:
                    _progress_set(row_id, f"{row_title} ｜ 已下载 {fetched} 个依赖包", None)
                continue
            if "npm http" in s:
                # npm 内部 http 日志（cache 命中/元数据/重试等）：不刷调试窗口
                continue
            if re.search(r"added \d+ packages?", s):
                # npm 报告完成（此时可能还有 audit/fund 等尾部输出）
                done_flag["done"] = True
                _progress_set(row_id, f"{row_title} ｜ 下载完成，正在写入 ...", 1.0, "100%")
                log_callback(f"[NPM输出] {s}")
            else:
                log_callback(f"[NPM输出] {s}")

    try:
        cmd = [NPM_CMD, "install", "-y", pkg_name, "--prefix", work_dir,
               "--loglevel=http", "--no-audit", "--no-fund"]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=BASE_DIR,
            creationflags=_no_window_flags(),
        )
    except FileNotFoundError:
        raise
    except Exception as e:
        log_callback(f"[安装异常] {str(e)}")
        return False

    t1 = threading.Thread(target=read_pipe, args=(proc.stdout,), daemon=True)
    t2 = threading.Thread(target=read_pipe, args=(proc.stderr,), daemon=True)
    t1.start()
    t2.start()
    ret = proc.wait()
    t1.join()
    t2.join()
    if ret == 0:
        if not done_flag["done"]:
            _progress_set(row_id, f"{row_title} ｜ ✅ 安装完成", 1.0, "100%")
        return True
    else:
        log_callback(f"[NPM安装] ❌ 退出码:{ret}")
        _progress_set(row_id, f"{row_title} ｜ ❌ 安装失败（退出码 {ret}）", 0.0, "失败")
        return False


def install_gateway(log_callback) -> bool:
    os.makedirs(GATEWAY_DIR, exist_ok=True)
    gateway_bin = get_gateway_bin()
    if os.path.exists(gateway_bin):
        log_callback("[网关] mcpgateway已存在，无需安装")
        _progress_set("gateway", "✅ 网关 mcpgateway 已存在，跳过安装", 1.0, "100%")
        return True

    log_callback("[网关] ==============================================")
    log_callback("[网关] 开始安装 mcpgateway (@michlyn/mcpgateway)")
    log_callback(f"[网关] 目录：{GATEWAY_DIR}")
    log_callback("[网关] ==============================================")
    size_str = format_size(query_package_size("@michlyn/mcpgateway"))
    row_title = "网关 mcpgateway" + (f" ｜ 下载包体" if size_str else "")
    _progress_set("gateway", f"{row_title} ｜ 正在解析依赖 ...", None)
    try:
        ok = _npm_install_progress(
            "@michlyn/mcpgateway@latest", GATEWAY_DIR, "gateway", row_title, log_callback,
        )
        if ok and os.path.exists(get_gateway_bin()):
            log_callback("[网关] ✅ mcpgateway 安装完成")
            _progress_set("gateway", f"✅ 网关 mcpgateway 安装完成（下载包体）"
                          if size_str else "✅ 网关 mcpgateway 安装完成",
                          1.0, "100%")
            return True
        log_callback("[网关] ❌ mcpgateway 安装失败")
        _progress_set("gateway", "❌ 网关 mcpgateway 安装失败", 0.0, "失败")
        return False
    except FileNotFoundError:
        log_callback("[致命错误] 未找到npm，请安装Node.js并配置环境变量！")
        _progress_set("gateway", "❌ 未找到 npm，请先安装 Node.js", 0.0, "失败")
        return False
    except Exception as e:
        log_callback(f"[网关安装异常] {str(e)}")
        return False


def extract_npx_package(cmd_cfg: dict) -> Tuple[bool, Optional[str]]:
    cmd = cmd_cfg.get("command", "")
    args = cmd_cfg.get("args", [])
    if cmd.lower() not in ("npx", "npx.cmd"):
        return False, None

    if len(args) < 1:
        return True, None

    if args[0] == "-y":
        if len(args) >= 2:
            return True, args[1]
        else:
            return True, None
    else:
        return True, args[0]


def install_mcp_to_workdir(work_dir: str, pkg_name: str, log_callback) -> bool:
    os.makedirs(work_dir, exist_ok=True)
    log_callback("[MCP安装] ==============================================")
    log_callback(f"[MCP安装] 正在安装 {pkg_name}")
    log_callback(f"[MCP安装] 目录：{work_dir}")
    log_callback("[MCP安装] ==============================================")
    size_str = format_size(query_package_size(pkg_name))
    row_title = pkg_name + (f" ｜ 下载包体" if size_str else "")
    _progress_set("pkg", f"{row_title} ｜ 正在解析依赖 ...", None)
    try:
        ok = _npm_install_progress(pkg_name, work_dir, "pkg", row_title, log_callback)
        if ok:
            log_callback(f"[MCP安装] ✅ {pkg_name} 安装完成！")
            _progress_set("pkg", f"✅ {pkg_name} 安装完成（下载包体）"
                          if size_str else f"✅ {pkg_name} 安装完成",
                          1.0, "100%")
            return True
        log_callback(f"[MCP安装] ❌ {pkg_name} 安装失败")
        _progress_set("pkg", f"❌ {pkg_name} 安装失败", 0.0, "失败")
        return False
    except FileNotFoundError:
        log_callback("[致命错误] 找不到npm，请先安装Node.js！")
        _progress_set("pkg", "❌ 未找到 npm，请先安装 Node.js", 0.0, "失败")
        return False
    except Exception as e:
        log_callback(f"[安装异常] {str(e)}")
        return False


class MCPServer:
    def __init__(self, name: str, port: int, config: dict):
        self.name = name
        self.port = port
        self.config = config
        self.running = False
        self.process: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None
        self.stdout_thread: Optional[threading.Thread] = None
        self.stderr_thread: Optional[threading.Thread] = None
        # Flet UI 控件引用（由界面创建时注入；后台线程更新控件是线程安全的）
        self.row: Optional[ft.Row] = None
        self.status_text: Optional[ft.Text] = None
        self.toggle_btn: Optional[ft.ElevatedButton] = None
        self.name_text: Optional[ft.Text] = None
        self.port_text: Optional[ft.Text] = None
        self.work_dir = get_server_work_dir(self.name)
        self._installing = False
        self.manual_cmd: Optional[str] = None  # 手动启动命令（留空=自动解析JS入口）

    def to_dict(self) -> dict:
        return {"name": self.name, "port": self.port, "config": self.config,
                "manual_cmd": self.manual_cmd}

    @classmethod
    def from_dict(cls, data: dict) -> "MCPServer":
        s = cls(data["name"], data["port"], data["config"])
        s.manual_cmd = data.get("manual_cmd") or None
        return s

    def get_raw_mcp_config(self) -> dict:
        if "mcpServers" in self.config and isinstance(self.config["mcpServers"], dict):
            mcp_servers = self.config["mcpServers"]
            first_key = next(iter(mcp_servers))
            return mcp_servers[first_key]
        return self.config

    def build_gateway_command(self, stdio_exec_path: str) -> list:
        gateway_exe = get_gateway_bin()
        if sys.platform == "win32":
            wrapped_cmd = f'"{stdio_exec_path}"'
        else:
            wrapped_cmd = stdio_exec_path
        return [
            gateway_exe,
            "--stdio", stdio_exec_path,
            "--outputTransport", "streamable-http",
            "--port", str(self.port),
        ]


    def start(self, log_callback):
        if self.running:
            log_callback(f"[{self.name}] 服务器已经在运行中")
            return False
        if self._installing:
            log_callback(f"[{self.name}] 正在执行依赖安装流程，请等待完成，请勿重复点击！")
            return False

        self._installing = True
        if self.toggle_btn is not None:
            self.toggle_btn.text = "安装中..."
            self.toggle_btn.disabled = True
            self.toggle_btn.update()

        # ========== 链式串行安装流水线 ==========
        def pipeline_install():
            try:
                log_callback(f"[{self.name}] 阶段1：检查并安装 mcpgateway")
                progress_queue.put({"type": "begin", "rows": [
                    {"id": "gateway", "title": f"[{self.name}] 正在安装网关 mcpgateway ..."},
                ]})
                gw_ok = install_gateway(log_callback)
                if not gw_ok:
                    log_callback(f"[{self.name}] ❌ 网关安装失败，流程终止")
                    _hide_progress_after(PROGRESS_HIDE_DELAY)
                    return

                mcp_cfg = self.get_raw_mcp_config()
                is_npx, pkg_name = extract_npx_package(mcp_cfg)
                if is_npx and pkg_name is not None:
                    entry = resolve_npx_entry(self.work_dir, pkg_name, log_callback)
                    if entry is None:
                        bin_dir = os.path.join(self.work_dir, "node_modules", ".bin")
                        entry = scan_mcp_cmd_in_bin(bin_dir, log_callback)
                    if entry is None:
                        log_callback(f"[{self.name}] 阶段2：本地未找到 {pkg_name}，开始安装")
                        progress_queue.put({"type": "add", "row": {
                            "id": "pkg", "title": f"[{self.name}] 正在安装 {pkg_name} ...",
                        }})
                        pkg_ok = install_mcp_to_workdir(self.work_dir, pkg_name, log_callback)
                        if not pkg_ok:
                            log_callback(f"[{self.name}] ❌ {pkg_name} 安装失败，流程终止")
                            _hide_progress_after(PROGRESS_HIDE_DELAY)
                            return

                log_callback(f"[{self.name}] ✅ 所有依赖准备就绪，自动启动服务...")
                _progress_set("gateway", f"✅ {self.name} 依赖就绪，正在启动服务 ...", 0.95, "95%")
                started = self._do_real_start(log_callback)
                if started:
                    _progress_set("gateway", f"✅ {self.name} 安装完成，服务已启动", 1.0, "100%")
                else:
                    _progress_set("gateway", f"❌ {self.name} 启动失败，请查看日志", 0.0, "失败")
                _hide_progress_after(PROGRESS_HIDE_DELAY)
            except Exception as e:
                log_callback(f"[{self.name}] ❌ 安装流程异常: {e}")
                _progress_set("gateway", f"❌ {self.name} 安装流程异常", 0.0, "失败")
                _hide_progress_after(PROGRESS_HIDE_DELAY)
            finally:
                self._installing = False
                self._do_update_ui()

        gateway_bin = get_gateway_bin()
        mcp_cfg = self.get_raw_mcp_config()
        is_npx, pkg_name = extract_npx_package(mcp_cfg)
        need_install = False
        if not os.path.exists(gateway_bin):
            need_install = True
        if is_npx and pkg_name is not None:
            # 静默检查是否已安装：bin 字段解析失败再退回扫描 .bin
            entry = resolve_npx_entry(self.work_dir, pkg_name)
            if entry is None:
                bin_dir = os.path.join(self.work_dir, "node_modules", ".bin")
                entry = scan_mcp_cmd_in_bin(bin_dir, log_callback)
            if entry is None:
                need_install = True

        if need_install:
            log_callback(f"[{self.name}] ⏳ 检测缺失依赖，启动完整安装流水线（网关→MCP包），完成后自动启动")
            threading.Thread(target=pipeline_install, daemon=True).start()
            return False
        else:
            self._installing = False
            return self._do_real_start(log_callback)

    def _do_real_start(self, log_callback):
        """真正执行启动逻辑"""
        mcp_cfg = self.get_raw_mcp_config()
        is_npx, pkg_name = extract_npx_package(mcp_cfg)
        stdio_exec_path = ""
        entry_js = None

        # ========== 启动入口解析 ==========
        # 优先级：手动启动命令/入口 > package.json bin 自动解析 > .bin 扫描兜底
        manual = (self.manual_cmd or "").strip()
        if manual:
            if (manual.lower().endswith((".js", ".cjs", ".mjs", ".njs"))
                    and os.path.isfile(manual)):
                entry_js = manual
                log_callback(f"[{self.name}] 使用手动JS入口：{entry_js}")
            else:
                stdio_exec_path = manual
                log_callback(f"[{self.name}] 使用手动启动命令：{stdio_exec_path}")
        elif is_npx and pkg_name is not None:
            # 方案3：主解析 package.json 的 bin 字段（权威、不猜文件名）
            entry_js = resolve_npx_entry(self.work_dir, pkg_name, log_callback)
            if entry_js is None:
                # 兜底：扫描 .bin 目录 + 解析 cmd shim（兼容 bin 字段异常的老包）
                bin_dir = os.path.join(self.work_dir, "node_modules", ".bin")
                target_cmd = scan_mcp_cmd_in_bin(bin_dir, log_callback)
                if target_cmd and os.path.exists(target_cmd):
                    entry_js = extract_js_from_npm_cmd(target_cmd, log_callback)
        else:
            cmd = mcp_cfg.get("command", "")
            args = mcp_cfg.get("args", [])
            quoted = []
            for p in ([cmd] + args):
                if " " in p:
                    quoted.append(f'"{p}"')
                else:
                    quoted.append(p)
            stdio_exec_path = " ".join(quoted)
            log_callback(f"[{self.name}] 非npx命令，直接执行: {stdio_exec_path}")

        # 有 JS 入口则统一用 node 启动
        if entry_js and not stdio_exec_path:
            if not os.path.isfile(entry_js):
                log_callback(f"[{self.name}] ❌ JS入口文件不存在：{entry_js}，启动终止")
                return False
            log_callback(f"[{self.name}] ✅ 找到JS入口：{entry_js}")
            args_raw = mcp_cfg.get("args", [])
            arg_index = 1
            if len(args_raw) > 0 and args_raw[0] == "-y":
                arg_index = 2
            tail_args = args_raw[arg_index:]
            cmd_parts = ["node", entry_js] + tail_args
            quoted_parts = []
            for part in cmd_parts:
                if " " in part:
                    quoted_parts.append(f'"{part}"')
                else:
                    quoted_parts.append(part)
            stdio_exec_path = " ".join(quoted_parts)
            log_callback(f"[{self.name}] 直接调用node启动：{stdio_exec_path}")
        elif not stdio_exec_path:
            log_callback(f"[{self.name}] ❌ 解析启动入口失败（package.json bin 与 .bin 扫描均未找到），启动终止")
            return False

        try:
            launch_cmd = self.build_gateway_command(stdio_exec_path)
            log_callback(f"[{self.name}] 网关启动命令: {' '.join(launch_cmd)}")

            creation_flags = 0
            if sys.platform == "win32":
                # 独立进程组 + 后台运行不弹 cmd 窗口（关窗会导致进程被终止）
                creation_flags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP | _no_window_flags()
                )

            launch_env = os.environ.copy()
            custom_env = mcp_cfg.get("env", {})
            if isinstance(custom_env, dict) and custom_env:
                launch_env.update(custom_env)
                log_callback(f"[{self.name}] 加载自定义环境变量键: {list(custom_env.keys())}")

            custom_cwd = mcp_cfg.get("cwd", None)
            run_cwd = BASE_DIR
            if custom_cwd and os.path.isdir(custom_cwd):
                run_cwd = custom_cwd
                log_callback(f"[{self.name}] 使用自定义工作目录: {run_cwd}")

            self.process = subprocess.Popen(
                launch_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
                text=True,
                encoding='utf-8',
                errors='replace',
                env=launch_env,
                cwd=run_cwd
            )
            self.pid = self.process.pid
            self.running = True
            register_proc(self.pid, self.name)  # 登记进程，退出前兜底清理（防端口残留）
            log_callback(f"[{self.name}] ✅ 进程已启动，PID: {self.pid}")

            self.stdout_thread = threading.Thread(
                target=self._read_output,
                args=(self.process.stdout, "stdout", log_callback),
                daemon=True
            )
            self.stderr_thread = threading.Thread(
                target=self._read_output,
                args=(self.process.stderr, "stderr", log_callback),
                daemon=True
            )
            self.stdout_thread.start()
            self.stderr_thread.start()

            monitor_thread = threading.Thread(
                target=self._monitor_process,
                args=(log_callback,),
                daemon=True
            )
            monitor_thread.start()

            self._update_ui_state()
            return True

        except FileNotFoundError as e:
            log_callback(f"[{self.name}] 启动文件不存在: {str(e)}")
            self.running = False
            self._update_ui_state()
            return False
        except Exception as e:
            log_callback(f"[{self.name}] 启动失败: {str(e)}")
            self.running = False
            self._update_ui_state()
            return False

    def _read_output(self, pipe, stream_type, log_callback):
        try:
            for line in iter(pipe.readline, ''):
                if line:
                    line_strip = line.rstrip()
                    if "[mcpgateway]" in line_strip and "No pending request" in line_strip:
                        continue
                    log_callback(f"[{self.name}] {line_strip}")
        except Exception as e:
            log_callback(f"[{self.name}] 读取 {stream_type} 出错: {e}")
        finally:
            pipe.close()

    def _monitor_process(self, log_callback):
        proc = self.process
        if proc is None:
            return
        try:
            proc.wait()
            unregister_proc(self.pid)  # 进程已结束，移出兜底清理登记表
            if self.running:
                self.running = False
                log_callback(f"[{self.name}] ⏹ 进程已退出 (PID: {self.pid})")
                self._update_ui_state()
        except Exception as e:
            log_callback(f"[{self.name}] 监控进程出错: {e}")

    def _update_ui_state(self):
        self._do_update_ui()

    def _do_update_ui(self):
        if self.status_text is None:
            return
        if self.running:
            self.status_text.value = "● 运行中"
            self.status_text.color = ft.Colors.GREEN_400
        else:
            self.status_text.value = "○ 已停止"
            self.status_text.color = ft.Colors.RED_400
        self.status_text.update()

        if self.toggle_btn is not None:
            if self._installing:
                self.toggle_btn.text = "安装中..."
                self.toggle_btn.disabled = True
            else:
                self.toggle_btn.disabled = False
                if self.running:
                    self.toggle_btn.text = "停止"
                    self.toggle_btn.bgcolor = COLOR_BTN_STOP
                    self.toggle_btn.color = COLOR_BTN_TEXT
                else:
                    self.toggle_btn.text = "启动"
                    self.toggle_btn.bgcolor = COLOR_BTN_START
                    self.toggle_btn.color = COLOR_BTN_TEXT
            self.toggle_btn.update()

    def stop(self, log_callback):
        if not self.running and self.process is None:
            log_callback(f"[{self.name}] 服务器未运行")
            return False

        try:
            if self.process is None:
                self.running = False
                self._update_ui_state()
                return True

            pid = self.process.pid
            log_callback(f"[{self.name}] 正在停止进程 PID: {pid}")

            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    check=False,
                    creationflags=_no_window_flags()
                )
            else:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except Exception:
                    self.process.terminate()

            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

            unregister_proc(self.pid)  # 进程已停止，移出兜底清理登记表
            self.running = False
            self.process = None
            self.pid = None
            log_callback(f"[{self.name}] ✅ 进程已停止")
            self._update_ui_state()
            return True

        except Exception as e:
            log_callback(f"[{self.name}] 停止失败: {str(e)}")
            self.running = False
            self._update_ui_state()
            return False

    def is_running(self) -> bool:
        return self.running


class MCPManagerApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.servers: List[MCPServer] = []
        self._server_list_height = SERVER_LIST_INIT_HEIGHT
        self._splitter_dragging = False

        page.title = "MCP管理器｜DeepSeek++ | Allen | 2.3 (mcpgateway)"
        page.window.width = MAIN_WINDOW_WIDTH
        page.window.height = MAIN_WINDOW_HEIGHT
        page.window.min_width = MAIN_WINDOW_MIN_WIDTH
        page.window.min_height = MAIN_WINDOW_MIN_HEIGHT
        page.window.resizable = True
        page.padding = 6
        page.spacing = 6

        # ---------- 深色主题 ----------
        page.theme_mode = ft.ThemeMode.DARK
        page.bgcolor = COLOR_BG

        # ---------- 日志缓冲：deque + 单个 selectable 文本控件，避免海量控件卡顿 ----------
        self._max_log_lines = MAX_LOG_LINES
        self._log_lines = deque(maxlen=MAX_LOG_LINES)
        self._log_text = ft.Text(
            selectable=True,
            font_family=LOG_FONT_FAMILY,
            size=FONT_SIZE_LOG,
            color=COLOR_LOG_FG,
        )
        self._log_text.value = "\n".join([
            "> mcpgateway 桥接模式｜2.3修复版 (Flet)",
            "> 安装全程锁定启动按钮，整套流程结束才释放",
            "> 串行安装：网关优先 → MCP服务包 → 自动启动",
            "> 拖动中间分隔条可调节服务器列表高度",
            "> 作者:Allen",
        ])
        self._stick_to_bottom = True  # 日志是否跟随自动滚底
        self._log_column = ft.Column(
            scroll=ft.ScrollMode.AUTO,
            auto_scroll=False,          # 手动控制滚底，避免与内容更新冲突
            expand=True,
            spacing=0,
            on_scroll=self._on_log_scroll,
            controls=[self._log_text],
        )

        # ---------- 安装进度区（调试输出面板顶部；每个程序一行：文字 + 百分比 + 进度条） ----------
        self._progress_rows = {}   # row_id -> {"label": ft.Text, "pct": ft.Text, "bar": ft.ProgressBar}
        self._progress_area = ft.Column(spacing=6, visible=False)

        try:
            self._create_widgets()
        except Exception as e:
            self._log(f"[系统] 界面构建失败（Flet {ft.version.version}）: {type(e).__name__}: {e}")

        self._load_servers()

        # ---------- 窗口关闭拦截：有服务运行先确认，再统一停止后退出 ----------
        # 注意：Flet 0.25.x 在 main() 首帧构建阶段设置 window.prevent_close 存在
        # 已知时序 bug（flet-dev/flet#5911），属性不会下发到客户端，导致点 ✕ 直接
        # 关窗、无任何提示。因此这里先立即设置一次（部分版本生效），再用定时器在
        # 首帧渲染完成后重新设置一次确保生效；另外模块级 atexit 兜底清理保证任何
        # 关闭路径都不会残留进程占用端口。
        self._close_guard_bound = False
        self._closing = False
        self._dialog_open = False
        # 立即设置一次：部分版本首帧即生效；但这里不置 _close_guard_bound，
        # 定时器仍会再发送一次并 update()，确保客户端真正应用 prevent_close（#5911）
        try:
            page.window.prevent_close = True
            page.window.on_event = self._on_window_event
        except Exception as e:
            self._log(f"[系统] 关闭拦截初始化失败: {e}")
        threading.Timer(CLOSE_GUARD_DELAY, self._enable_close_guard).start()

        # ---------- 日志刷新线程：后台批量拉取队列，长时间运行不卡 UI ----------
        threading.Thread(target=self._log_flusher, daemon=True).start()

        # ---------- 启动后检查端口占用（防残留进程占端口） ----------
        if CHECK_PORT_ON_START:
            threading.Thread(target=self._check_ports, daemon=True).start()

        self._log(f"[系统] Flet {ft.version.version} 启动 | 已加载 {len(self.servers)} 个服务器配置")
        self._log(f"[系统] 启动耗时 {time.time() - _START_TIME:.1f} 秒"
                  + ("（已启用快速图标加载）" if _FAST_ICONS_OK else "（图标加载未加速）"))
        self._log(f"[系统] 项目地址：https://github.com/2091k/ai-mcp-server")

    # ---------- 界面构建 ----------
    def _create_widgets(self):
        # 顶部工具条
        toolbar = ft.Row([
            ft.Text("MCP 服务器", size=FONT_SIZE_TITLE,
                    weight=ft.FontWeight.BOLD, color=COLOR_TEXT),
            ft.Container(expand=True),
            ft.OutlinedButton(
                "清除日志", height=BTN_HEIGHT,
                icon=ft.Icons.DELETE_SWEEP,
                style=ft.ButtonStyle(
                    padding=ft.padding.symmetric(horizontal=12),
                    text_style=ft.TextStyle(size=FONT_SIZE_BUTTON, weight=ft.FontWeight.BOLD),
                ),
                on_click=self._clear_log,
            ),
            ft.FilledButton(
                "＋ 添加", height=BTN_HEIGHT,
                bgcolor=COLOR_BTN_ADD, color=COLOR_BTN_TEXT,
                style=ft.ButtonStyle(
                    padding=ft.padding.symmetric(horizontal=12),
                    text_style=ft.TextStyle(size=FONT_SIZE_BUTTON, weight=ft.FontWeight.BOLD),
                ),
                on_click=self._add_server_dialog,
            ),
        ])

        # 服务器列表（固定高度、内部滚动；高度可拖动分隔条调节）
        op_width = BTN_WIDTH * 3 + ROW_SPACING * 2
        header = ft.Row([
            ft.Text("名称", width=COL_NAME_WIDTH, size=FONT_SIZE_HEADER,
                    weight=ft.FontWeight.BOLD, color=COLOR_TEXT_DIM),
            ft.Text("端口", width=COL_PORT_WIDTH, size=FONT_SIZE_HEADER,
                    weight=ft.FontWeight.BOLD, color=COLOR_TEXT_DIM),
            ft.Text("状态", width=COL_STATUS_WIDTH, size=FONT_SIZE_HEADER,
                    weight=ft.FontWeight.BOLD, color=COLOR_TEXT_DIM),
            ft.Text("操作", width=op_width, size=FONT_SIZE_HEADER,
                    weight=ft.FontWeight.BOLD, color=COLOR_TEXT_DIM),
        ], spacing=ROW_SPACING)
        self._server_list = ft.ListView(spacing=4, auto_scroll=False)
        self._server_panel = ft.Container(
            height=self._server_list_height,
            bgcolor=COLOR_PANEL,
            border=ft.border.all(1, COLOR_PANEL_BORDER),
            border_radius=ft.border_radius.all(8),
            padding=8,
            content=ft.Column([
                header,
                ft.Divider(height=1, color=COLOR_PANEL_BORDER),
                ft.Container(content=self._server_list, expand=True),
            ], spacing=4),
        )

        # 分隔条：按住上下拖动可调整服务器列表高度
        splitter = ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.RESIZE_ROW,
            on_pan_start=self._on_splitter_start,
            on_pan_update=self._on_splitter_update,
            content=ft.Container(
                height=SPLITTER_HEIGHT,
                bgcolor=COLOR_SPLITTER,
                border_radius=ft.border_radius.all(3),
                alignment=ft.alignment.center,
                content=ft.Icon(ft.Icons.DRAG_HANDLE,
                                size=SPLITTER_ICON_SIZE, color=COLOR_TEXT_DIM),
            ),
        )

        # 调试输出面板（深色背景，可上下滚动，自动滚底）
        log_panel = ft.Container(
            expand=True,
            bgcolor=COLOR_LOG_BG,
            border=ft.border.all(1, COLOR_PANEL_BORDER),
            border_radius=ft.border_radius.all(8),
            padding=8,
            content=ft.Column([
                ft.Row([
                    ft.Text("调试输出", size=FONT_SIZE_TOOLBAR,
                            weight=ft.FontWeight.BOLD, color=COLOR_TEXT_DIM)
                ]),
                ft.Divider(height=1, color=COLOR_PANEL_BORDER),
                self._progress_area,
                self._log_column,
            ], spacing=4),
        )

        self.page.add(toolbar, self._server_panel, splitter, log_panel)

    # ---------- 分隔条拖动 ----------
    def _on_splitter_start(self, e):
        self._splitter_dragging = True

    def _on_splitter_update(self, e):
        new_h = self._server_list_height + (e.delta_y or 0)
        new_h = max(SERVER_LIST_MIN_HEIGHT, min(SERVER_LIST_MAX_HEIGHT, new_h))
        self._server_list_height = new_h
        self._server_panel.height = new_h
        try:
            self._server_panel.update()
        except Exception:
            pass

    # ---------- 服务器列表 ----------
    def _add_server_ui(self, server: MCPServer, update: bool = True):
        name_text = ft.Text(server.name, width=COL_NAME_WIDTH, size=FONT_SIZE_LIST,
                            weight=ft.FontWeight.W_600, color=COLOR_TEXT,
                            overflow=ft.TextOverflow.ELLIPSIS, tooltip=server.name)
        port_text = ft.Text(str(server.port), width=COL_PORT_WIDTH,
                            size=FONT_SIZE_LIST, color=COLOR_TEXT)
        status_text = ft.Text("○ 已停止", width=COL_STATUS_WIDTH,
                              size=FONT_SIZE_LIST, color=ft.Colors.RED_400)

        # 三个按钮统一样式：横向文字、同宽同高（编辑/删除不再竖排）
        btn_style = ft.ButtonStyle(
            padding=ft.padding.symmetric(horizontal=4, vertical=0),
            text_style=ft.TextStyle(size=FONT_SIZE_BUTTON, weight=ft.FontWeight.BOLD),
        )
        toggle_btn = ft.ElevatedButton(
            "启动", width=BTN_WIDTH, height=BTN_HEIGHT,
            bgcolor=COLOR_BTN_START, color=COLOR_BTN_TEXT,
            style=btn_style,
            on_click=lambda e: self._toggle_server(server),
        )
        edit_btn = ft.ElevatedButton(
            "编辑", width=BTN_WIDTH, height=BTN_HEIGHT,
            bgcolor=COLOR_BTN_EDIT, color=COLOR_BTN_TEXT,
            style=btn_style,
            on_click=lambda e: self._edit_server(server),
        )
        del_btn = ft.ElevatedButton(
            "删除", width=BTN_WIDTH, height=BTN_HEIGHT,
            bgcolor=COLOR_BTN_DELETE, color=COLOR_BTN_TEXT,
            style=btn_style,
            on_click=lambda e: self._confirm_delete(server),
        )

        row = ft.Row([
            name_text, port_text, status_text, toggle_btn, edit_btn, del_btn,
        ], spacing=ROW_SPACING)

        server.row = row
        server.name_text = name_text
        server.port_text = port_text
        server.status_text = status_text
        server.toggle_btn = toggle_btn

        self._server_list.controls.append(row)
        if update:
            try:
                self._server_list.update()
            except Exception:
                pass

    def _toggle_server(self, server: MCPServer):
        if server.running:
            server.stop(self._log)
        else:
            server.start(self._log)

    def _confirm_delete(self, server: MCPServer):
        if server.running:
            msg = (f"服务器 '{server.name}' 正在运行，确定删除并停止？\n"
                   f"⚠不会自动删除本地 {server.name} 文件夹，需手动清理")
        else:
            msg = (f"确定删除服务器 '{server.name}'？\n"
                   f"⚠不会自动删除本地 {server.name} 文件夹")

        def do_delete(e):
            self.page.close(dialog)
            if server.running:
                server.stop(self._log)
            self.servers.remove(server)
            if server.row in self._server_list.controls:
                self._server_list.controls.remove(server.row)
            self._save_servers()
            self._log(f"[系统] 已删除服务器配置: {server.name}")
            self._server_list.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("确认删除", size=FONT_SIZE_DIALOG_TITLE, weight=ft.FontWeight.BOLD),
            content=ft.Text(msg, size=FONT_SIZE_DIALOG),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("删除", on_click=do_delete),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)


    # ---------- 添加 / 编辑 ----------
    def _add_server_dialog(self, e):
        name_tf = ft.TextField(label="名称（文件夹名）", value="", autofocus=True,
                               text_size=FONT_SIZE_DIALOG)
        port_tf = ft.TextField(label="端口", value="9999",
                               keyboard_type=ft.KeyboardType.NUMBER,
                               text_size=FONT_SIZE_DIALOG)
        cmd_tf = ft.TextField(
            label="启动命令（可选，留空自动解析JS入口）",
            value="",
            text_size=FONT_SIZE_DIALOG,
            hint_text="JS文件路径或完整命令；留空自动读package.json bin",
        )
        config_tf = ft.TextField(
            label="JSON 配置",
            multiline=True,
            min_lines=8,
            max_lines=14,
            text_size=FONT_SIZE_DIALOG,
            value=json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
        )

        def do_add(e):
            name = (name_tf.value or "").strip()
            port_str = (port_tf.value or "").strip()
            config_str = (config_tf.value or "").strip()

            if not name:
                self._show_error("错误", "请输入服务器名称（将作为文件夹名称）")
                return
            try:
                port = int(port_str)
                if port < 1 or port > 65535:
                    raise ValueError
            except ValueError:
                self._show_error("错误", "端口号必须是 1-65535 的整数")
                return

            for s in self.servers:
                if s.name == name:
                    self._show_error("错误", f"已存在名称为 '{name}' 的服务器")
                    return

            try:
                config = json.loads(config_str)
            except json.JSONDecodeError as err:
                self._show_error("JSON 错误", f"JSON格式错误:\n{str(err)}")
                return

            server = MCPServer(name, port, config)
            server.manual_cmd = (cmd_tf.value or "").strip() or None
            self.servers.append(server)
            self._add_server_ui(server)
            self._save_servers()
            self._log(f"[系统] 已添加服务器: {name} (端口: {port})")
            self.page.close(dialog)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("添加服务器", size=FONT_SIZE_DIALOG_TITLE, weight=ft.FontWeight.BOLD),
            content=ft.Column(
                [name_tf, port_tf, cmd_tf, config_tf],
                tight=True, width=440, scroll=ft.ScrollMode.AUTO,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("保存", on_click=do_add),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)

    def _edit_server(self, server: MCPServer):
        name_tf = ft.TextField(label="名称（文件夹名）", value=server.name,
                               text_size=FONT_SIZE_DIALOG)
        port_tf = ft.TextField(label="端口", value=str(server.port),
                               keyboard_type=ft.KeyboardType.NUMBER,
                               text_size=FONT_SIZE_DIALOG)
        cmd_tf = ft.TextField(
            label="启动命令（可选，留空自动解析JS入口）",
            value=server.manual_cmd or "",
            text_size=FONT_SIZE_DIALOG,
            hint_text="JS文件路径或完整命令；留空自动读package.json bin",
        )
        config_tf = ft.TextField(
            label="JSON 配置",
            multiline=True,
            min_lines=8,
            max_lines=14,
            text_size=FONT_SIZE_DIALOG,
            value=json.dumps(server.config, indent=2, ensure_ascii=False),
        )

        def do_save(e):
            new_name = (name_tf.value or "").strip()
            port_str = (port_tf.value or "").strip()
            config_str = (config_tf.value or "").strip()

            if not new_name:
                self._show_error("错误", "请输入服务器名称")
                return
            try:
                new_port = int(port_str)
                if new_port < 1 or new_port > 65535:
                    raise ValueError
            except ValueError:
                self._show_error("错误", "端口号必须是 1-65535 的整数")
                return

            try:
                new_config = json.loads(config_str)
            except json.JSONDecodeError as err:
                self._show_error("JSON 错误", f"JSON格式错误:\n{str(err)}")
                return

            old_name = server.name
            server.name = new_name
            server.port = new_port
            server.config = new_config
            server.manual_cmd = (cmd_tf.value or "").strip() or None
            server.work_dir = get_server_work_dir(new_name)

            if server.name_text is not None:
                server.name_text.value = new_name
            if server.port_text is not None:
                server.port_text.value = str(new_port)
            if server.row is not None:
                server.row.update()

            self._save_servers()
            self._log(f"[系统] 已更新服务器: {old_name} -> {new_name}")
            self.page.close(dialog)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("编辑服务器", size=FONT_SIZE_DIALOG_TITLE, weight=ft.FontWeight.BOLD),
            content=ft.Column(
                [name_tf, port_tf, cmd_tf, config_tf],
                tight=True, width=440, scroll=ft.ScrollMode.AUTO,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("保存", on_click=do_save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)

    def _show_error(self, title: str, message: str):
        def on_close(e):
            self.page.close(dlg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(title, size=FONT_SIZE_DIALOG_TITLE, weight=ft.FontWeight.BOLD),
            content=ft.Text(message, size=FONT_SIZE_DIALOG),
            actions=[ft.TextButton("确定", on_click=on_close)],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dlg)

    # ---------- 配置持久化 ----------
    def _save_servers(self):
        data = [s.to_dict() for s in self.servers]
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log(f"[系统] 保存配置失败: {e}")

    def _load_servers(self):
        """加载配置：单条异常不影响其它条目，保证"添加"等功能始终可用"""
        if not os.path.exists(CONFIG_FILE):
            self._log("[系统] 未找到配置文件，请点击 [＋添加] 创建服务器")
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._log(f"[系统] 读取配置失败: {type(e).__name__}: {e}")
            return
        if not isinstance(data, list):
            self._log("[系统] 配置格式错误：mcp_servers.json 应为数组，请检查")
            return

        ok = 0
        for item in data:
            try:
                server = MCPServer.from_dict(item)
                self._add_server_ui(server, update=False)   # 启动时批量构建，最后统一刷新
                self.servers.append(server)
                ok += 1
            except Exception as e:
                name = item.get("name", "?") if isinstance(item, dict) else "?"
                self._log(f"[系统] 跳过无效配置项 [{name}]: {type(e).__name__}: {e}")
        try:
            self._server_list.update()
        except Exception:
            pass
        self._log(f"[系统] 已加载 {ok}/{len(data)} 个服务器配置")

    # ---------- 日志 ----------
    def _clear_log(self, e):
        """清空调试日志（含队列中尚未刷新的消息）"""
        try:
            while True:
                log_queue.get_nowait()
        except queue.Empty:
            pass
        self._log_lines.clear()
        self._log_text.value = ""
        try:
            self._log_text.update()
        except Exception:
            pass
        self._log("[系统] 日志已清除")

    def _log(self, message: str):
        log_queue.put(message)

    def _log_flusher(self):
        """后台刷新线程：批量拉取日志队列与进度队列 → 单控件更新，长时间运行不卡 UI"""
        while True:
            time.sleep(LOG_FLUSH_INTERVAL)
            batch = []
            try:
                while True:
                    batch.append(log_queue.get_nowait())
            except queue.Empty:
                pass
            if batch:
                self._append_log_lines(batch)
            self._flush_progress()

    def _make_progress_row(self, row_id: str, title: str):
        """创建一行进度：左侧文字（含包体大小/下载进度），右侧百分比，下方进度条"""
        label = ft.Text(title, size=FONT_SIZE_TOOLBAR, color=COLOR_TEXT_DIM,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
        pct = ft.Text("", size=FONT_SIZE_TOOLBAR, color=PROGRESS_BAR_COLOR,
                      weight=ft.FontWeight.BOLD, width=64, text_align=ft.TextAlign.RIGHT)
        bar = ft.ProgressBar(value=None, bar_height=PROGRESS_BAR_HEIGHT,
                             color=PROGRESS_BAR_COLOR, bgcolor=PROGRESS_BAR_TRACK)
        self._progress_rows[row_id] = {"label": label, "pct": pct, "bar": bar}
        return ft.Column([
            ft.Row([label, pct], spacing=4),
            bar,
        ], spacing=2)

    def _flush_progress(self):
        """处理进度条消息队列：begin=重建行列表 / add=追加一行 / set=更新一行 / hide=隐藏"""
        changed = False
        try:
            while True:
                msg = progress_queue.get_nowait()
                mtype = msg.get("type")
                if mtype in ("begin", "add"):
                    if mtype == "begin":
                        # 新的一轮安装：清空旧行重新构建（多服务器并发时以最后启动者为准）
                        self._progress_area.controls.clear()
                        self._progress_rows.clear()
                        rows = msg.get("rows", [])
                    else:
                        rows = [msg.get("row", {})]
                    for r in rows:
                        self._progress_area.controls.append(
                            self._make_progress_row(r.get("id", ""), r.get("title", "")))
                    self._progress_area.visible = True
                elif mtype == "set":
                    row = self._progress_rows.get(msg.get("id"))
                    if row is not None:
                        if msg.get("label") is not None:
                            row["label"].value = msg["label"]
                        if msg.get("pct") is not None:
                            row["pct"].value = msg["pct"]
                        row["bar"].value = msg.get("value")  # None -> 不确定进度（动画）
                elif mtype == "hide":
                    self._progress_area.visible = False
                    self._progress_rows.clear()
                    self._progress_area.controls.clear()
                changed = True
        except queue.Empty:
            pass
        if changed:
            try:
                self._progress_area.update()
            except Exception:
                pass

    def _append_log_lines(self, lines):
        for line in lines:
            self._log_lines.append(line)
        self._log_text.value = "\n".join(self._log_lines)
        try:
            self._log_text.update()
            # 自动滚动到底部（用户向上翻阅时 stick 为 False，不打扰）
            if LOG_STICK_BOTTOM and self._stick_to_bottom:
                self._log_column.scroll_to(offset=-1)
        except Exception:
            pass

    def _on_log_scroll(self, e):
        """根据用户滚动位置决定是否继续自动滚底"""
        try:
            if e.max_scroll_extent is None or e.viewport_dimension is None:
                return
            if e.pixels >= e.max_scroll_extent - LOG_SCROLL_TOLERANCE:
                self._stick_to_bottom = True
            else:
                self._stick_to_bottom = False
        except Exception:
            pass

    # ---------- 端口占用检查 ----------
    def _check_ports(self):
        if not CHECK_PORT_ON_START:
            return
        for s in list(self.servers):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.4)
                    if sock.connect_ex(("127.0.0.1", s.port)) == 0:
                        self._log(f"[系统] ⚠ 端口 {s.port}（{s.name}）已被占用，可能上次退出未停止进程，请先手动停止或清理残留进程")
            except Exception:
                pass

    # ---------- 停止所有服务器 ----------
    def _stop_all_servers(self):
        self._log("[系统] 正在停止所有服务器...")
        for s in self.servers:
            if s.running or s.process is not None:
                try:
                    s.stop(self._log)
                except Exception as e:
                    self._log(f"[系统] 停止 {s.name} 失败: {e}")
        # 兜底：强制清理仍登记在册但未能正常停止的进程
        kill_all_registered_procs()

    # ---------- 窗口关闭（统一安全退出） ----------
    def _safe_close(self, force: bool = False):
        """统一安全退出：先停止所有进程，再销毁窗口，保证端口不残留。
        force=True 表示用户已确认退出（跳过再次弹窗）。"""
        if self._closing:
            return
        running = [s for s in self.servers if s.running]
        if not force and running and CONFIRM_CLOSE_WITH_RUNNING:
            if not self._dialog_open:
                self._dialog_open = True
                self._confirm_close_dialog(running)
            return
        self._closing = True
        # 1. 先停止所有服务器进程（含进程树），避免端口被占用、下次无法启动
        try:
            self._stop_all_servers()
        except Exception as e:
            self._log(f"[系统] 退出前停止进程出错: {e}")
        # 2. 解除关闭拦截，避免 Flet 在确认后卡住不关闭（flet-dev/flet#3918）
        try:
            self.page.window.prevent_close = False
            self.page.window.on_event = None
            self.page.update()
        except Exception:
            pass
        # 3. 真正销毁窗口
        try:
            self.page.window.destroy()
        except Exception:
            pass

    def _on_window_event(self, e):
        """Flet 窗口事件（prevent_close 生效时，点 ✕ 会触发 close 事件）"""
        if e.data != "close":
            return
        self._safe_close(force=False)

    def _enable_close_guard(self):
        """首帧渲染完成后重新启用关闭拦截（规避 Flet 0.25 在 main 回调内设置失效的 bug）"""
        if self._closing or self._close_guard_bound:
            return
        try:
            self.page.window.prevent_close = True
            self.page.window.on_event = self._on_window_event
            self.page.update()
            self._close_guard_bound = True
            self._log("[系统] 关闭拦截已启用")
        except Exception as e:
            self._log(f"[系统] 关闭拦截延迟启用失败: {e}")

    def _confirm_close_dialog(self, running):
        """有服务运行时关闭窗口：弹窗提示进程正常运行中，确认后再停止并退出"""
        names = "、".join(s.name for s in running[:5])
        if len(running) > 5:
            names += f" 等 {len(running)} 个"
        msg = (f"检测到 {len(running)} 个服务器进程正在运行：\n{names}\n\n"
               f"直接关闭会导致端口被占用、下次无法启动。\n"
               f"是否先停止所有进程再退出？")

        def on_confirm(e):
            try:
                self.page.close(dialog)
            except Exception:
                pass
            self._safe_close(force=True)

        def on_cancel(e):
            try:
                self.page.close(dialog)
            except Exception:
                pass
            self._dialog_open = False

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("确认退出", size=FONT_SIZE_DIALOG_TITLE, weight=ft.FontWeight.BOLD),
            content=ft.Text(msg, size=FONT_SIZE_DIALOG),
            actions=[
                ft.TextButton("取消", on_click=on_cancel),
                ft.FilledButton("退出并停止", on_click=on_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dialog)


# =====================================================================
# 打包/启动辅助：错误日志 + 弹窗提示
# （避免 exe 静默失败、出现"无窗口后台运行"却无任何提示）
# =====================================================================
def _app_error_log_path():
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR
    return os.path.join(base, "mcp-server-error.log")


def _write_error_log(msg: str):
    try:
        with open(_app_error_log_path(), "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def _show_error_dialog(title: str, message: str):
    if sys.platform == "win32" and ctypes is not None:
        try:
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
            return
        except Exception:
            pass
    try:
        print(title + "\n" + message)
    except Exception:
        pass


def _check_desktop_runtime() -> bool:
    """检查 flet 桌面运行时(flet.exe 及数据)是否可用，冻结(exe)打包校验用"""
    try:
        import flet_desktop
        import flet_desktop.version  # noqa: F401
        exe_name = "flet.exe" if sys.platform == "win32" else "flet"
        exe_path = os.path.join(
            os.path.dirname(os.path.abspath(flet_desktop.__file__)),
            "app", "flet", exe_name,
        )
        return os.path.isfile(exe_path)
    except Exception:
        return False


def main(page: ft.Page):
    MCPManagerApp(page)


if __name__ == "__main__":
    # PyInstaller 冻结环境兼容：必须在创建任何子进程前调用（无副作用，可放心保留）
    try:
        import multiprocessing
        multiprocessing.freeze_support()
    except Exception:
        pass

    # 冻结(exe)环境：提前检查 Flet 桌面运行时是否被打包。
    # 若缺失，flet 会尝试联网下载 Flutter 客户端 -> 导致"无窗口、后台进程挂起"。
    if getattr(sys, "frozen", False) and not _check_desktop_runtime():
        _show_error_dialog(
            "启动失败：打包缺少 Flet 桌面运行时",
            "未找到 flet_desktop 桌面运行时(flet.exe 及其数据文件)。\n\n"
            "请用以下命令重新打包：\n"
            "pyinstaller --onedir --windowed --name MCP-Manager "
            "--collect-all flet --collect-all flet_desktop mcp-server.py\n\n"
            "并把 mcp_servers.json 复制到 exe 同目录。",
        )
        sys.exit(1)

    # MCP_HEADLESS=1 时以隐藏窗口启动（用于无人值守/自动化测试）
    view = ft.AppView.FLET_APP_HIDDEN if os.environ.get("MCP_HEADLESS") == "1" \
        else ft.AppView.FLET_APP

    try:
        ft.app(target=main, view=view)
    except SystemExit:
        raise
    except Exception:
        # 兜底：任何未处理异常都写日志并弹窗提示，绝不"静默后台运行"
        err = traceback.format_exc()
        _write_error_log(err)
        _show_error_dialog(
            "程序运行出错",
            "发生未处理异常，详情已写入日志：\n" + _app_error_log_path() + "\n\n" + err,
        )
        sys.exit(1)
