#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP管理器重构版（优化版）2.0
改动：
1. 批量日志处理，避免 UI 卡顿
2. 调试窗口限制最大行数（5000行）
3. 用户滚动时暂停自动滚动，5秒后恢复
4. 保留原有功能：自动扫描node_modules/.bin下名称含mcp的cmd文件，打印文件内容到调试窗口
   找不到则不再降级运行原始npx全局命令，直接启动失败
5. 修复关键点：pathlib拼接..\路径异常跳转到根目录BUG
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import json
import os
import subprocess
import threading
import queue
import sys
import signal
import re
from pathlib import Path
from typing import List, Optional, Tuple

# ===================== 界面参数 =====================
DEBUG_FONT_SIZE = 11
DEBUG_FONT_NAME = "Consolas"
DEBUG_BG_COLOR = "#161b22"
DEBUG_FG_COLOR = "#00ff00"
DEBUG_CURSOR_COLOR = "#58a6ff"
MAIN_WINDOW_WIDTH = 720
MAIN_WINDOW_HEIGHT = 700
# ====================================================

CONFIG_FILE = "mcp_servers.json"
DEFAULT_CONFIG = {
    "mcpServers": {
        "demo-mcp": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"]
        }
    }
}

log_queue = queue.Queue()
BASE_DIR = os.path.abspath(os.path.dirname(sys.argv[0]))
GATEWAY_DIR = os.path.join(BASE_DIR, "gateway")

if sys.platform == "win32":
    NPM_CMD = "npm.cmd"
    SCRIPT_EXT = ".cmd"
else:
    NPM_CMD = "npm"
    SCRIPT_EXT = ""


def strip_pkg_version(pkg_full: str) -> str:
    """
    剥离包名尾部版本标签
    @playwright/mcp@latest → @playwright/mcp
    xlsx@0.18.5 → xlsx
    """
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
    return os.path.join(GATEWAY_DIR, "node_modules", ".bin", f"supergateway{SCRIPT_EXT}")


def get_server_bat_path(work_dir: str) -> str:
    return os.path.join(work_dir, "start-mcp.bat")


def write_clean_mcp_bat(bat_path: str, js_entry: str):
    """生成纯净单层启动脚本，直接node调用js，附带参数转发 %*"""
    content = '@echo off\nnode "{}" %*\n'.format(js_entry)
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(content)


def scan_mcp_cmd_in_bin(bin_dir: str, log_callback) -> Optional[str]:
    """
    在node_modules/.bin 目录搜索文件名包含 mcp 的 .cmd 文件
    返回第一个匹配到的文件路径，无匹配返回None
    """
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
    """
    解析 npm 生成的 .cmd 文件，提取真实 JS 入口
    修复：去除 rel_js 开头的 \ 或 /，避免 os.path.join 误认为绝对路径
    """
    log_callback(f"[解析] 准备读取cmd文件路径：{cmd_file_path}")
    if not os.path.exists(cmd_file_path):
        log_callback(f"[解析] 文件不存在！")
        return None

    try:
        with open(cmd_file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        log_callback(f"[解析] 读取cmd失败: {str(e)}")
        return None

    log_callback("=" * 60)
    log_callback(f"[cmd原始内容开始] {cmd_file_path}")
    log_callback(content)
    log_callback(f"[cmd原始内容结束]")
    log_callback("=" * 60)

    bin_dir = os.path.dirname(cmd_file_path)  # 例如 D:\bt\mcp\chrome\node_modules\.bin
    log_callback(f"[解析] .bin 目录：{bin_dir}")

    # 主正则：匹配 "%dp0%xxx.js"（可能带 ..\ 或 ../
    pat1 = re.compile(r'"%dp0%([^"]+\.(js|njs))"')
    match = pat1.search(content)
    if match:
        raw_rel = match.group(1)
        # 去除前导的 \ 或 /（Windows 下可能是 \..\，Linux 可能是 /../）
        rel_js = raw_rel.lstrip('\\/')
        log_callback(f"[解析] 主正则匹配到 raw_rel: {raw_rel} → 处理后: {rel_js}")
        temp_path = os.path.join(bin_dir, rel_js)
        abs_js = os.path.abspath(temp_path)
        log_callback(f"[解析] 组合后路径：{temp_path}")
        log_callback(f"[解析] 规范化绝对路径：{abs_js}")
        if os.path.isfile(abs_js):
            log_callback(f"[解析] ✅ JS入口有效：{abs_js}")
            return abs_js
        else:
            log_callback(f"[解析] ⚠️ 该路径不存在，继续尝试备用正则")

    # 备用正则：匹配不带 %dp0% 的 .js 路径
    pat2 = re.compile(r'["\s]([^\s"]+\.(js|njs))["\s]')
    candidates = pat2.findall(content)
    for rel, ext in candidates:
        if rel.startswith('%dp0%'):
            continue
        # 同样去除前导分隔符
        rel_clean = rel.lstrip('\\/')
        temp_path = os.path.join(bin_dir, rel_clean)
        abs_js = os.path.abspath(temp_path)
        if os.path.isfile(abs_js):
            log_callback(f"[解析] 备用正则匹配到JS入口：{abs_js}")
            return abs_js

    log_callback("[解析] ❌ 未能从cmd脚本找到有效JS入口")
    return None


def install_gateway(log_callback) -> bool:
    os.makedirs(GATEWAY_DIR, exist_ok=True)
    gateway_bin = get_gateway_bin()
    if os.path.exists(gateway_bin):
        log_callback("[网关] supergateway已存在，无需安装")
        return True

    log_callback("[网关] ==============================================")
    log_callback(f"[网关] 开始安装 supergateway")
    log_callback(f"[网关] 目录：{GATEWAY_DIR}")
    log_callback("[网关] ==============================================")
    try:
        cmd = [
            NPM_CMD,
            "install",
            "supergateway@latest",
            "--prefix", GATEWAY_DIR
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=BASE_DIR
        )

        def read_pipe(pipe):
            for line in iter(pipe.readline, ""):
                if line.strip():
                    log_callback(f"[NPM输出] {line.rstrip()}")

        t1 = threading.Thread(target=read_pipe, args=(proc.stdout,), daemon=True)
        t2 = threading.Thread(target=read_pipe, args=(proc.stderr,), daemon=True)
        t1.start()
        t2.start()
        ret = proc.wait()
        t1.join()
        t2.join()
        if ret == 0 and os.path.exists(get_gateway_bin()):
            log_callback("[网关] ✅ supergateway 安装完成")
            return True
        else:
            log_callback(f"[网关] ❌ supergateway 安装失败，退出码:{ret}")
            return False
    except FileNotFoundError:
        log_callback("[致命错误] 未找到npm，请安装Node.js并配置环境变量！")
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
    try:
        cmd = [
            NPM_CMD,
            "install",
            "-y",
            pkg_name,
            "--prefix", work_dir
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=BASE_DIR
        )

        def read_pipe(pipe):
            for line in iter(pipe.readline, ""):
                if line.strip():
                    log_callback(f"[NPM输出] {line.rstrip()}")

        t1 = threading.Thread(target=read_pipe, args=(proc.stdout,), daemon=True)
        t2 = threading.Thread(target=read_pipe, args=(proc.stderr,), daemon=True)
        t1.start()
        t2.start()
        ret = proc.wait()
        t1.join()
        t2.join()
        if ret == 0:
            log_callback(f"[MCP安装] ✅ {pkg_name} 安装完成！")
            return True
        else:
            log_callback(f"[MCP安装] ❌ {pkg_name} 安装失败，退出码:{ret}")
            return False
    except FileNotFoundError:
        log_callback("[致命错误] 找不到npm，请先安装Node.js！")
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
        self.ui_frame: Optional[tk.Frame] = None
        self.status_label: Optional[tk.Label] = None
        self.toggle_btn: Optional[tk.Button] = None
        self.name_label: Optional[ttk.Label] = None
        self.port_label: Optional[ttk.Label] = None
        self.work_dir = get_server_work_dir(self.name)

    def to_dict(self) -> dict:
        return {"name": self.name, "port": self.port, "config": self.config}

    @classmethod
    def from_dict(cls, data: dict) -> "MCPServer":
        return cls(data["name"], data["port"], data["config"])

    def get_raw_mcp_config(self) -> dict:
        if "mcpServers" in self.config and isinstance(self.config["mcpServers"], dict):
            mcp_servers = self.config["mcpServers"]
            first_key = next(iter(mcp_servers))
            return mcp_servers[first_key]
        return self.config

    def build_gateway_command(self, stdio_exec_path: str) -> list:
        gateway_exe = get_gateway_bin()
        return [
            gateway_exe,
            "--stdio", stdio_exec_path,
            "--outputTransport", "streamableHttp",
            "--port", str(self.port)
        ]

    def start(self, log_callback):
        if self.running:
            log_callback(f"[{self.name}] 服务器已经在运行中")
            return False

        if not os.path.exists(get_gateway_bin()):
            log_callback(f"[{self.name}] 网关未安装，开始自动安装...")
            ok = install_gateway(log_callback)
            if not ok:
                log_callback(f"[{self.name}] supergateway准备失败，无法启动")
                return False

        mcp_cfg = self.get_raw_mcp_config()
        is_npx, pkg_name = extract_npx_package(mcp_cfg)
        stdio_exec_path = ""

        if is_npx and pkg_name is not None:
            bin_dir = os.path.join(self.work_dir, "node_modules", ".bin")
            # 自动扫描包含mcp的cmd，不再硬编码包名拼接
            target_cmd = scan_mcp_cmd_in_bin(bin_dir, log_callback)

            if target_cmd is None:
                log_callback(f"[{self.name}] .bin目录未找到mcp相关cmd，开始安装包 {pkg_name}")
                ok = install_mcp_to_workdir(self.work_dir, pkg_name, log_callback)
                if not ok:
                    log_callback(f"[{self.name}] {pkg_name} 安装失败，无法启动")
                    return False
                # 安装完成再次扫描
                target_cmd = scan_mcp_cmd_in_bin(bin_dir, log_callback)

            if target_cmd and os.path.exists(target_cmd):
                entry_js = extract_js_from_npm_cmd(target_cmd, log_callback)
                if entry_js and os.path.exists(entry_js):
                    log_callback(f"[{self.name}] ✅ 找到JS入口：{entry_js}")
                    bat_path = get_server_bat_path(self.work_dir)
                    write_clean_mcp_bat(bat_path, entry_js)
                    stdio_exec_path = bat_path
                    log_callback(f"[{self.name}] 生成启动脚本: {bat_path}")
                else:
                    log_callback(f"[{self.name}] ❌ 解析JS入口失败，不支持直接调用cmd，启动终止")
                    return False
            else:
                log_callback(f"[{self.name}] ❌ 本地目录未找到mcp cmd文件，取消全局npx降级，启动终止")
                return False
        else:
            # 非npx原生命令保持原有逻辑不变
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

        try:
            launch_cmd = self.build_gateway_command(stdio_exec_path)
            log_callback(f"[{self.name}] 网关启动命令: {' '.join(launch_cmd)}")

            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

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
                    log_callback(f"[{self.name}] {line.rstrip()}")
        except Exception as e:
            log_callback(f"[{self.name}] 读取 {stream_type} 出错: {e}")
        finally:
            pipe.close()

    def _monitor_process(self, log_callback):
        if self.process is None:
            return
        try:
            self.process.wait()
            if self.running:
                self.running = False
                log_callback(f"[{self.name}] ⏹ 进程已退出 (PID: {self.pid})")
                self._update_ui_state()
        except Exception as e:
            log_callback(f"[{self.name}] 监控进程出错: {e}")

    def _update_ui_state(self):
        if self.ui_frame is None:
            return
        self.ui_frame.after(0, self._do_update_ui)

    def _do_update_ui(self):
        if self.status_label:
            if self.running:
                self.status_label.config(text="● 运行中", foreground="green")
            else:
                self.status_label.config(text="○ 已停止", foreground="red")
        if self.toggle_btn:
            if self.running:
                self.toggle_btn.config(text="停止", bg="#ff6b6b")
            else:
                self.toggle_btn.config(text="启动", bg="#51cf66")

    def stop(self, log_callback):
        if not self.running:
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
                    check=False
                )
            else:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except:
                    self.process.terminate()

            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

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
    def __init__(self, root):
        self.root = root
        self.root.title("MCP管理器｜DeepSeek++ | Allen | 2.0")
        self.root.geometry(f"{MAIN_WINDOW_WIDTH}x{MAIN_WINDOW_HEIGHT}")
        self.root.resizable(True, True)

        self.servers: List[MCPServer] = []
        self._bind_wheel_id = None
        self._create_widgets()
        self._load_servers()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # ---------- 优化相关属性 ----------
        self._max_log_lines = 5000          # 最大行数
        self._user_scrolled = False         # 用户是否手动滚动了
        # 绑定滚动条事件
        self.debug_text.vbar.bind("<ButtonPress-1>", self._on_user_scroll)
        self.debug_text.vbar.bind("<ButtonRelease-1>", self._on_user_scroll_release)
        # 启动日志处理循环（每50ms）
        self.root.after(50, self._process_log_queue)

    def _create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="6")
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(main_frame)
        toolbar.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(toolbar, text="MCP 服务器", font=("Arial", 13, "bold")).pack(side=tk.LEFT)

        add_btn = tk.Button(
            toolbar, text="＋ 添加", bg="#339af0", fg="white",
            font=("Arial", 10, "bold"), relief=tk.RAISED, bd=2,
            command=self._add_server_dialog
        )
        add_btn.pack(side=tk.RIGHT)

        vert_sash = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        vert_sash.pack(fill=tk.BOTH, expand=True)

        list_frame = ttk.LabelFrame(vert_sash, text="服务器列表", padding="4")
        vert_sash.add(list_frame, weight=3)

        self.list_canvas = tk.Canvas(list_frame, highlightthickness=0)
        self.list_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.list_canvas.yview)
        self.list_inner = ttk.Frame(self.list_canvas)

        self.list_canvas.configure(yscrollcommand=self.list_scrollbar.set)
        self.canvas_window = self.list_canvas.create_window((0, 0), window=self.list_inner, anchor="nw")

        def _sync_inner_width(event):
            self.list_canvas.itemconfig(self.canvas_window, width=event.width)
        self.list_canvas.bind("<Configure>", _sync_inner_width)
        self.list_inner.bind("<Configure>", lambda e: self._update_scrollregion())

        self.list_canvas.bind("<Enter>", self._bind_mousewheel)
        self.list_canvas.bind("<Leave>", self._unbind_mousewheel)

        self.list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.list_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        header_frame = ttk.Frame(self.list_inner)
        header_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(header_frame, text="名称", width=10, font=("Arial", 9, "bold")).grid(row=0, column=0, padx=(0,4), sticky="w")
        ttk.Label(header_frame, text="端口", width=6, font=("Arial", 9, "bold")).grid(row=0, column=1, padx=(0,4), sticky="w")
        ttk.Label(header_frame, text="状态", width=8, font=("Arial", 9, "bold")).grid(row=0, column=2, padx=(0,4), sticky="w")
        ttk.Label(header_frame, text="操作", width=18, font=("Arial", 9, "bold")).grid(row=0, column=3, sticky="w")

        ttk.Separator(self.list_inner, orient="horizontal").pack(fill=tk.X, pady=(0, 4))
        self.server_entries_frame = ttk.Frame(self.list_inner)
        self.server_entries_frame.pack(fill=tk.X)

        debug_frame = ttk.LabelFrame(vert_sash, text="调试输出", padding="4")
        vert_sash.add(debug_frame, weight=2)

        self.debug_text = scrolledtext.ScrolledText(
            debug_frame, wrap=tk.WORD,
            font=(DEBUG_FONT_NAME, DEBUG_FONT_SIZE),
            bg=DEBUG_BG_COLOR, fg=DEBUG_FG_COLOR, insertbackground=DEBUG_CURSOR_COLOR
        )
        self.debug_text.pack(fill=tk.BOTH, expand=True)
        self.debug_text.config(state=tk.NORMAL)
        self.debug_text.insert(tk.END, "> 自动扫描node_modules/.bin内名称含mcp的cmd文件\n> 自动打印cmd完整源码到调试窗口\n> 作者:Allen 项目地址：https://github.com/2091k/ai-mcp-server\n> 首次启动对应服务会安装程序，请等待\n")
        self.debug_text.config(state=tk.DISABLED)
        self.debug_text.see(tk.END)

    def _on_mousewheel(self, event):
        delta = int(-1 * (event.delta / 120))
        self.list_canvas.yview_scroll(delta, "units")

    def _bind_mousewheel(self, event):
        self._bind_wheel_id = self.root.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        if self._bind_wheel_id is not None:
            self.root.unbind_all("<MouseWheel>")
            self._bind_wheel_id = None

    def _update_scrollregion(self):
        self.list_canvas.update_idletasks()
        self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

    def _add_server_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("添加服务器")
        dialog.geometry("360x460")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="名称(文件夹名):").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        name_entry = ttk.Entry(dialog, width=30)
        name_entry.grid(row=0, column=1, padx=8, pady=4, sticky="ew")

        ttk.Label(dialog, text="端口:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        port_entry = ttk.Entry(dialog, width=10)
        port_entry.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        port_entry.insert(0, "9999")

        ttk.Label(dialog, text="JSON 配置:").grid(row=2, column=0, sticky="nw", padx=8, pady=4)
        config_text = scrolledtext.ScrolledText(dialog, height=10, font=("Consolas", 9))
        config_text.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        config_text.insert("1.0", json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False))

        dialog.columnconfigure(1, weight=1)

        def do_add():
            name = name_entry.get().strip()
            port_str = port_entry.get().strip()
            config_str = config_text.get("1.0", tk.END).strip()

            if not name:
                messagebox.showerror("错误", "请输入服务器名称（将作为文件夹名称）")
                return
            try:
                port = int(port_str)
                if port < 1 or port > 65535:
                    raise ValueError
            except ValueError:
                messagebox.showerror("错误", "端口号必须是 1-65535 的整数")
                return

            for s in self.servers:
                if s.name == name:
                    messagebox.showerror("错误", f"已存在名称为 '{name}' 的服务器")
                    return

            try:
                config = json.loads(config_str)
            except json.JSONDecodeError as e:
                messagebox.showerror("JSON 错误", f"JSON格式错误:\n{str(e)}")
                return

            server = MCPServer(name, port, config)
            self.servers.append(server)
            self._add_server_ui(server)
            self._save_servers()
            self._log(f"[系统] 已添加服务器: {name} (端口: {port})")
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text="保存", command=do_add).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=6)

    def _edit_server(self, server: MCPServer):
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑服务器")
        dialog.geometry("360x460")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="名称(文件夹名):").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        name_entry = ttk.Entry(dialog, width=30)
        name_entry.grid(row=0, column=1, padx=8, pady=4, sticky="ew")
        name_entry.insert(0, server.name)

        ttk.Label(dialog, text="端口:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        port_entry = ttk.Entry(dialog, width=10)
        port_entry.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        port_entry.insert(0, str(server.port))

        ttk.Label(dialog, text="JSON 配置:").grid(row=2, column=0, sticky="nw", padx=8, pady=4)
        config_text = scrolledtext.ScrolledText(dialog, height=10, font=("Consolas", 9))
        config_text.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        config_text.insert("1.0", json.dumps(server.config, indent=2, ensure_ascii=False))

        dialog.columnconfigure(1, weight=1)

        def do_save():
            new_name = name_entry.get().strip()
            port_str = port_entry.get().strip()
            config_str = config_text.get("1.0", tk.END).strip()

            if not new_name:
                messagebox.showerror("错误", "请输入服务器名称")
                return
            try:
                new_port = int(port_str)
                if new_port < 1 or new_port > 65535:
                    raise ValueError
            except ValueError:
                messagebox.showerror("错误", "端口号必须是 1-65535 的整数")
                return

            for s in self.servers:
                if s is not server and s.name == new_name:
                    messagebox.showerror("错误", f"已存在名称为 '{new_name}' 的服务器")
                    return

            try:
                new_config = json.loads(config_str)
            except json.JSONDecodeError as e:
                messagebox.showerror("JSON 错误", f"JSON格式错误:\n{str(e)}")
                return

            old_name = server.name
            server.name = new_name
            server.port = new_port
            server.config = new_config
            server.work_dir = get_server_work_dir(new_name)

            if server.name_label:
                server.name_label.config(text=new_name)
            if server.port_label:
                server.port_label.config(text=str(new_port))

            self._save_servers()
            self._log(f"[系统] 已更新服务器: {old_name} -> {new_name}")
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text="保存", command=do_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=6)

    def _add_server_ui(self, server: MCPServer):
        frame = ttk.Frame(self.server_entries_frame)
        frame.pack(fill=tk.X, pady=2)

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)
        frame.columnconfigure(2, weight=0)
        frame.columnconfigure(3, weight=0)
        frame.columnconfigure(4, weight=0)
        frame.columnconfigure(5, weight=0)

        name_label = ttk.Label(frame, text=server.name, width=10, anchor="w")
        name_label.grid(row=0, column=0, padx=(0,4), sticky="w")

        port_label = ttk.Label(frame, text=str(server.port), width=6, anchor="w")
        port_label.grid(row=0, column=1, padx=(0,4), sticky="w")

        status_label = ttk.Label(frame, text="○ 已停止", foreground="red", width=8, anchor="w")
        status_label.grid(row=0, column=2, padx=(0,4), sticky="w")

        toggle_btn = tk.Button(
            frame, text="启动", bg="#51cf66", fg="white",
            font=("Arial", 9, "bold"), width=6,
            relief=tk.RAISED, bd=2,
            command=lambda: self._toggle_server(server)
        )
        toggle_btn.grid(row=0, column=3, padx=(0,2))

        edit_btn = tk.Button(
            frame, text="编辑", bg="#fcc419", fg="white",
            font=("Arial", 9, "bold"), width=6,
            relief=tk.RAISED, bd=2,
            command=lambda: self._edit_server(server)
        )
        edit_btn.grid(row=0, column=4, padx=(0,2))

        del_btn = tk.Button(
            frame, text="删除", bg="#ff6b6b", fg="white",
            font=("Arial", 9, "bold"), width=6,
            relief=tk.RAISED, bd=2,
            command=lambda: self._delete_server(server)
        )
        del_btn.grid(row=0, column=5)

        server.ui_frame = frame
        server.name_label = name_label
        server.port_label = port_label
        server.status_label = status_label
        server.toggle_btn = toggle_btn
        server._do_update_ui()
        self._update_scrollregion()

    def _toggle_server(self, server: MCPServer):
        if server.running:
            server.stop(self._log)
        else:
            server.start(self._log)

    def _delete_server(self, server: MCPServer):
        if server.running:
            reply = messagebox.askyesno(
                "确认删除",
                f"服务器 '{server.name}' 正在运行，确定删除并停止？\n⚠不会自动删除本地 {server.name} 文件夹，需手动清理"
            )
            if not reply:
                return
            server.stop(self._log)
        else:
            reply = messagebox.askyesno(
                "确认删除",
                f"确定删除服务器 '{server.name}'？\n⚠不会自动删除本地 {server.name} 文件夹"
            )
            if not reply:
                return

        self.servers.remove(server)
        if server.ui_frame:
            server.ui_frame.destroy()
        self._save_servers()
        self._log(f"[系统] 已删除服务器配置: {server.name}")
        self._update_scrollregion()

    def _save_servers(self):
        data = [s.to_dict() for s in self.servers]
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log(f"[系统] 保存配置失败: {e}")

    def _load_servers(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                server = MCPServer.from_dict(item)
                self.servers.append(server)
                self._add_server_ui(server)
            self._log(f"[系统] 已加载 {len(self.servers)} 个服务器配置")
        except Exception as e:
            self._log(f"[系统] 加载配置失败: {e}")

    def _log(self, message: str):
        log_queue.put(message)

    # ---------- 优化后的日志处理 ----------
    def _process_log_queue(self):
        # 批量取出所有消息
        messages = []
        try:
            while True:
                msg = log_queue.get_nowait()
                messages.append(msg)
        except queue.Empty:
            pass

        if messages:
            self.debug_text.config(state=tk.NORMAL)
            # 一次性插入
            text_to_insert = "".join(msg + "\n" for msg in messages)
            self.debug_text.insert(tk.END, text_to_insert)

            # 限制最大行数
            line_count = int(self.debug_text.index('end-1c').split('.')[0])
            if line_count > self._max_log_lines:
                del_lines = line_count - self._max_log_lines
                self.debug_text.delete('1.0', f'{del_lines+1}.0')

            self.debug_text.config(state=tk.DISABLED)
            # 仅在用户未滚动时自动滚动
            if not self._user_scrolled:
                self.debug_text.see(tk.END)

        # 继续调度
        self.root.after(50, self._process_log_queue)

    def _on_user_scroll(self, event):
        self._user_scrolled = True

    def _on_user_scroll_release(self, event):
        # 5秒后恢复自动滚动
        self.root.after(5000, lambda: setattr(self, '_user_scrolled', False))

    def _on_closing(self):
        running_count = sum(1 for s in self.servers if s.running)
        if running_count > 0:
            reply = messagebox.askyesno(
                "确认退出",
                f"有 {running_count} 个服务器正在运行，退出前将自动停止它们。"
            )
            if not reply:
                return

        self._log("[系统] 正在停止所有服务器...")
        for s in self.servers:
            if s.running:
                s.stop(self._log)
        self.root.destroy()


def main():
    root = tk.Tk()
    app = MCPManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()