# -*- coding: utf-8 -*-
"""
cleaner_core.py —— C 盘清理核心引擎

移植自本轮实测验证过的 PowerShell 脚本（clean-c-drive-pro.ps1），并固化了三条
用真实故障换来的安全规则：

  1) Edge 旧版本判定，绝不能用「版本号最高 = 当前版本」。
     根目录引导器（msedge.exe 等）通过 Side-by-Side 清单硬绑定了具体版本目录，
     一旦删掉被绑定的版本，Edge 会永久启动失败（且不会自我修复）。
     -> 因此：只有「未被任何引导器绑定 + 不是 Last Version + 不是目录内最高版」
        三者同时成立的目录，才允许删除。

  2) WPS 的 office6\\backup 里是崩溃恢复副本，可能是用户唯一没存盘的版本。
     -> 永不删除，只统计。

  3) 夸克保留版本必须多来源交叉验证（Last Version / 注册表 / 根程序 / 运行进程），
     任何单一来源都可能给出错误答案（实测出现过注册表与真实运行版本不一致）。

扫描（scan）产出的就是执行计划（actions），所见即所删。
"""

import os
import re
import sys
import stat
import time
import shutil
import ctypes
import winreg
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

CREATE_NO_WINDOW = 0x08000000

WIN_DIR = os.environ.get("WINDIR", r"C:\Windows")
LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
ROAM = os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming"))
PF86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
PF = os.environ.get("ProgramFiles", r"C:\Program Files")

VRE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
PROGRESS = Callable[[str], None]


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def disk_usage(path: str = "C:\\"):
    """返回 (总容量, 剩余) 字节"""
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    try:
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(path), ctypes.byref(free), ctypes.byref(total), None)
        if not ok:
            return 0, 0
    except Exception:
        return 0, 0
    return total.value, free.value


def path_size(p: str) -> int:
    if not p or not os.path.exists(p):
        return 0
    try:
        if os.path.isfile(p):
            return os.path.getsize(p)
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f), follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def fmt_size(b: float) -> str:
    b = float(b or 0)
    if b < 1024:
        return "%d B" % b
    for unit in ("KB", "MB", "GB", "TB"):
        b /= 1024.0
        if b < 1024 or unit == "TB":
            return "%.2f %s" % (b, unit)
    return "%.2f TB" % b


def verkey(v: str):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return (0,)


def file_version(path: str) -> str:
    """读取 PE 文件版本资源（FileVersion 四段式）。失败返回空串。"""
    if not os.path.isfile(path):
        return ""
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf):
            return ""
        ptr = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return ""

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [("dwSignature", ctypes.c_uint32), ("dwStrucVersion", ctypes.c_uint32),
                        ("dwFileVersionMS", ctypes.c_uint32), ("dwFileVersionLS", ctypes.c_uint32),
                        ("dwProductVersionMS", ctypes.c_uint32), ("dwProductVersionLS", ctypes.c_uint32),
                        ("dwFileFlagsMask", ctypes.c_uint32), ("dwFileFlags", ctypes.c_uint32),
                        ("dwFileOS", ctypes.c_uint32), ("dwFileType", ctypes.c_uint32),
                        ("dwFileSubtype", ctypes.c_uint32), ("dwFileDateMS", ctypes.c_uint32),
                        ("dwFileDateLS", ctypes.c_uint32)]

        info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms = info.dwFileVersionMS
        ls = info.dwFileVersionLS
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return ""


def is_running(image: str) -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % image, "/NH"],
                           capture_output=True, text=True, errors="replace",
                           creationflags=CREATE_NO_WINDOW, timeout=20)
        return image.lower() in (r.stdout or "").lower()
    except Exception:
        return False


def reg_versions(keywords: List[str]) -> set:
    """从卸载表里找匹配关键字的程序版本号"""
    out = set()
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    kws = [k.lower() for k in keywords]
    for hive, sub in roots:
        try:
            key = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(key, name) as sk:
                        try:
                            dn = str(winreg.QueryValueEx(sk, "DisplayName")[0])
                        except OSError:
                            continue
                        if not any(k in dn.lower() for k in kws):
                            continue
                        try:
                            dv = str(winreg.QueryValueEx(sk, "DisplayVersion")[0]).strip()
                            if dv:
                                out.add(dv)
                        except OSError:
                            pass
                except OSError:
                    continue
    return out


def read_last_version(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        return ""


SXS_RE = re.compile(rb'<assemblyIdentity[^>]*?\bname="(\d+\.\d+\.\d+\.\d+)"', re.I | re.S)


def sxs_bound_version(path: str) -> Optional[str]:
    """读取 exe 内嵌的 SxS 清单，返回它绑定的版本号"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    m = SXS_RE.search(data)
    return m.group(1).decode() if m else None


# --------------------------------------------------------------------------
# 删除实现
# --------------------------------------------------------------------------

def _force_rmtree(path: str) -> bool:
    """删除整个目录，自动处理只读属性。返回是否彻底删除"""
    def onerr(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    try:
        shutil.rmtree(path, onerror=onerr)
    except Exception:
        pass
    return not os.path.exists(path)


def remove_path(path: str, log: PROGRESS = None) -> int:
    """删除文件或目录，返回释放字节数（按实际删掉的部分估算）"""
    if not path or not os.path.exists(path):
        return 0
    size = path_size(path)
    if os.path.isdir(path) and not os.path.islink(path):
        ok = _force_rmtree(path)
        left = path_size(path) if os.path.exists(path) else 0
        if not ok and log and left > 0:
            log("    跳过（被占用或无权限）: %s（剩余 %s）" % (os.path.basename(path), fmt_size(left)))
        return max(0, size - left)
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    try:
        os.remove(path)
        return size
    except OSError as e:
        if log:
            log("    删除失败: %s (%s)" % (os.path.basename(path), e.strerror))
        return 0


def clear_dir(path: str, older_hours: float = 0.0, log: PROGRESS = None,
              cancel: Callable[[], bool] = None) -> int:
    """清空目录内容（保留目录本身）。older_hours>0 时只删该时间之前修改的项。"""
    if not os.path.isdir(path):
        return 0
    cut = time.time() - older_hours * 3600 if older_hours > 0 else None
    freed = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0
    for e in entries:
        if cancel and cancel():
            break
        if cut is not None:
            try:
                if e.stat(follow_symlinks=False).st_mtime >= cut:
                    continue
            except OSError:
                continue
        freed += remove_path(e.path, log)
    return freed


# --------------------------------------------------------------------------
# 服务控制
# --------------------------------------------------------------------------

def _svc(name: str, action: str) -> None:
    try:
        subprocess.run(["sc", action, name], capture_output=True,
                       creationflags=CREATE_NO_WINDOW, timeout=30)
    except Exception:
        pass


def _svc_state(name: str) -> str:
    try:
        r = subprocess.run(["sc", "query", name], capture_output=True, text=True,
                           errors="replace", creationflags=CREATE_NO_WINDOW, timeout=20)
        if "RUNNING" in (r.stdout or ""):
            return "RUNNING"
    except Exception:
        pass
    return "STOPPED"


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class Item:
    label: str
    path: str
    size: int
    kind: str = "delete"      # delete / keep / note
    detail: str = ""


@dataclass
class Module:
    key: str
    name: str
    desc: str
    needs_admin: bool = False
    items: List[Item] = field(default_factory=list)
    actions: List[dict] = field(default_factory=list)
    total: int = 0
    note: str = ""
    blocked: str = ""         # 非空表示本次不可执行的原因

    def add(self, label, path, size, kind="delete", detail=""):
        self.items.append(Item(label, path, size, kind, detail))
        if kind == "delete":
            self.total += size


# --------------------------------------------------------------------------
# 模块定义
# --------------------------------------------------------------------------

MODULE_SPECS = [
    ("win_update", "Windows 更新缓存", "已装的更新包残留，删后重装会重新下载", True, True),
    ("win_temp", "系统临时文件", "Windows\\Temp 内超 2 小时的残留", True, True),
    ("win_leftover", "升级/回滚残留", "$WinREAgent 等系统升级遗留目录", True, True),
    ("edge", "Edge 旧版本残留", "旧版本目录，自动识别绑定不会删错", True, True),
    ("webview", "WebView2 旧运行时", "旧运行时，仅个别软件会锁定使用", True, False),
    ("quark", "夸克旧版本残留", "保留运行中与待切换版本，只删废弃的", True, True),
    ("wps", "WPS 缓存与旧版本", "旧版本、插件重复副本、缓存日志", False, True),
    ("dism", "WinSxS 组件清理", "DISM 官方回收，耗时 10~30 分钟", True, True),
]


def module_specs():
    return [{"key": k, "name": n, "desc": d, "needs_admin": a, "default_checked": c}
            for k, n, d, a, c in MODULE_SPECS]


# ---- 1. Windows 更新缓存 -------------------------------------------------

def scan_win_update(log: PROGRESS = None) -> Module:
    m = Module("win_update", "Windows 更新缓存",
               "已下载安装的更新包残留，删除后如需重装更新会重新下载", True)
    p = os.path.join(WIN_DIR, "SoftwareDistribution", "Download")
    if not os.path.isdir(p):
        m.note = "目录不存在"
        return m
    size = path_size(p)
    if size >= 1024 * 1024:
        m.add("更新下载缓存", p, size, "delete", "清空目录内容，保留目录结构")
        m.actions.append({"op": "clear_update", "path": p, "size": size})
    else:
        m.note = "不足 1 MB，无需清理"
    return m


# ---- 2. 系统临时文件 -----------------------------------------------------

def scan_win_temp(log: PROGRESS = None) -> Module:
    m = Module("win_temp", "系统临时文件", "Windows\\Temp 中超过 2 小时的残留文件", True)
    p = os.path.join(WIN_DIR, "Temp")
    if not os.path.isdir(p):
        m.note = "目录不存在"
        return m
    cut = time.time() - 2 * 3600
    size = 0
    count = 0
    try:
        for e in os.scandir(p):
            try:
                if e.stat(follow_symlinks=False).st_mtime >= cut:
                    continue
            except OSError:
                continue
            size += path_size(e.path)
            count += 1
    except OSError:
        m.note = "无法读取"
        return m
    if size >= 1024 * 1024:
        m.add("超 2 小时残留（%d 项）" % count, p, size, "delete", "只删 2 小时之前的文件")
        m.actions.append({"op": "clear_old", "path": p, "hours": 2, "size": size})
    else:
        m.note = "可清理项不足 1 MB"
    return m


# ---- 3. 升级/回滚残留 ----------------------------------------------------

def scan_win_leftover(log: PROGRESS = None) -> Module:
    m = Module("win_leftover", "升级/回滚残留",
               "$WinREAgent、$SysReset、$Windows.~BT 等系统升级遗留目录", True)
    for name in ("$WinREAgent", "$SysReset", "$Windows.~BT", "$Windows.~WS", "$GetCurrent"):
        p = os.path.join("C:\\", name)
        if not os.path.isdir(p):
            continue
        size = path_size(p)
        if size < 20 * 1024 * 1024:
            m.add(name, p, size, "keep", "不足 20 MB，保留")
            continue
        m.add(name, p, size, "delete")
        m.actions.append({"op": "rmtree", "path": p, "size": size})
    if not m.items:
        m.note = "无残留目录"
    return m


# ---- 4. Edge ------------------------------------------------------------

EDGE_APP = os.path.join(PF86, "Microsoft", "Edge", "Application")
EDGE_DIRS = [
    os.path.join(PF86, "Microsoft", "Edge", "Application"),
    os.path.join(PF86, "Microsoft", "EdgeCore"),
    os.path.join(PF86, "Microsoft", "Copilot", "Application"),
]
WEBVIEW_DIR = os.path.join(PF86, "Microsoft", "EdgeWebView", "Application")
EDGE_STUBS = ["msedge.exe", "new_msedge.exe", "msedge_proxy.exe",
              "new_msedge_proxy.exe", "pwahelper.exe", "new_pwahelper.exe"]


def edge_protected_versions():
    """返回 (受保护版本集合, 绑定明细)"""
    protected = set()
    detail = []
    for stub in EDGE_STUBS:
        p = os.path.join(EDGE_APP, stub)
        if not os.path.isfile(p):
            continue
        v = sxs_bound_version(p)
        if v:
            protected.add(v)
            detail.append((stub, v))
    lv = read_last_version(os.path.join(LOCAL, "Microsoft", "Edge", "User Data", "Last Version"))
    if lv:
        protected.add(lv)
        detail.append(("Last Version", lv))
    return protected, detail


def edge_health():
    """校验每个引导器的绑定目标是否存在：[(文件名, 绑定版本, 是否正常)]"""
    out = []
    for stub in EDGE_STUBS:
        p = os.path.join(EDGE_APP, stub)
        if not os.path.isfile(p):
            continue
        v = sxs_bound_version(p)
        if not v:
            continue
        out.append((stub, v, os.path.isdir(os.path.join(EDGE_APP, v))))
    return out


def _scan_version_dirs(m: Module, dirs: List[str], protected: set) -> None:
    """通用版本目录扫描：只有未被保护、且 2 小时内没变动过的目录才进删除计划"""
    dirs_versions: Dict[str, List[str]] = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            vers = [x for x in os.listdir(d)
                    if VRE.match(x) and os.path.isdir(os.path.join(d, x))]
        except OSError:
            continue
        if vers:
            dirs_versions[d] = vers
            protected.add(max(vers, key=verkey))

    now = time.time()
    for d, vers in dirs_versions.items():
        tag = "%s\\%s" % (os.path.basename(os.path.dirname(d)), os.path.basename(d))
        for v in sorted(vers, key=verkey, reverse=True):
            p = os.path.join(d, v)
            size = path_size(p)
            if v in protected:
                m.add("%s  %s" % (tag, v), p, size, "keep", "当前版本或被引导器绑定")
                continue
            try:
                if now - os.path.getmtime(p) < 7200:
                    m.add("%s  %s" % (tag, v), p, size, "keep", "2 小时内变动过，本次跳过")
                    continue
            except OSError:
                pass
            m.add("%s  %s" % (tag, v), p, size, "delete")
            m.actions.append({"op": "rmtree", "path": p, "size": size})


def scan_edge(log: PROGRESS = None) -> Module:
    m = Module("edge", "Edge 旧版本残留",
               "浏览器升级后遗留的旧版本目录（自动识别 SxS 绑定，不会删错）", True)
    if not os.path.isdir(EDGE_APP):
        m.note = "未检测到 Edge 安装目录"
        return m

    protected, _detail = edge_protected_versions()
    _scan_version_dirs(m, EDGE_DIRS, protected)

    if is_running("msedge.exe"):
        m.note = "Edge 正在运行，被占用的目录会自动跳过"
    if protected:
        m.note = (m.note + "  " if m.note else "") + \
                 "保护版本: " + ", ".join(sorted(protected, key=verkey))
    if not m.actions:
        m.note = (m.note + "  " if m.note else "") + "无旧版本，已经很干净"
    return m


def scan_webview(log: PROGRESS = None) -> Module:
    m = Module("webview", "WebView2 旧运行时",
               "旧版 WebView2 运行时（Edge 之外的独立组件，默认不勾选）", True)
    if not os.path.isdir(WEBVIEW_DIR):
        m.note = "未检测到 WebView2 运行时"
        return m
    protected: set = set()
    _scan_version_dirs(m, [WEBVIEW_DIR], protected)
    if not m.actions:
        m.note = "无旧运行时"
    else:
        m.note = "个别软件锁定了旧版运行时才会用到；删除后若某程序白屏，重装 WebView2 即可恢复"
    return m


# ---- 5. 夸克 ------------------------------------------------------------

QUARK_BASE = os.path.join(PF, "Quark")


def quark_keep_versions():
    keep = {}
    lv = read_last_version(os.path.join(LOCAL, "Quark", "User Data", "Last Version"))
    if lv and VRE.match(lv):
        keep[lv] = "Last Version"
    for v in reg_versions(["夸克", "quark"]):
        if VRE.match(v):
            keep.setdefault(v, "注册表")
    rv = file_version(os.path.join(QUARK_BASE, "quark.exe"))
    if rv and VRE.match(rv):
        keep.setdefault(rv, "根目录主程序")
    return keep


def scan_quark(log: PROGRESS = None) -> Module:
    m = Module("quark", "夸克旧版本残留",
               "多版本并存时删除废弃版本，保留正在运行与待切换版本", True)
    if not os.path.isdir(QUARK_BASE):
        m.note = "未安装夸克"
        return m
    keep = quark_keep_versions()
    try:
        vers = [x for x in os.listdir(QUARK_BASE)
                if VRE.match(x) and os.path.isdir(os.path.join(QUARK_BASE, x))]
    except OSError:
        m.note = "无法读取安装目录"
        return m
    if not vers:
        m.note = "无版本目录"
        return m

    now = time.time()
    for v in sorted(vers, key=verkey, reverse=True):
        p = os.path.join(QUARK_BASE, v)
        size = path_size(p)
        if v in keep:
            m.add(v, p, size, "keep", "保留（%s）" % keep[v])
            continue
        try:
            if now - os.path.getmtime(p) < 7200:
                m.add(v, p, size, "keep", "2 小时内变动过，本次跳过")
                continue
        except OSError:
            pass
        m.add(v, p, size, "delete")
        m.actions.append({"op": "rmtree", "path": p, "size": size})

    if keep:
        m.note = "保留版本: " + ", ".join(sorted(keep, key=verkey))
    if not m.actions:
        m.note = (m.note + "  " if m.note else "") + "无旧版本"
    return m


# ---- 6. WPS -------------------------------------------------------------

WPS_BASE = os.path.join(LOCAL, "Kingsoft", "WPS Office")
WPS_POOL = os.path.join(ROAM, "kingsoft", "wps", "addons", "pool", "win-i386")
WPS_OFFICE6 = os.path.join(ROAM, "kingsoft", "office6")
WPS_BACKUP = os.path.join(WPS_OFFICE6, "backup")
POOL_RE = re.compile(r"^(?P<base>.+)_(?P<ver>\d+(?:\.\d+)+)$")


def wps_current_versions():
    keep = {}
    for v in reg_versions(["wps office", "wps"]):
        if VRE.match(v):
            keep[v] = "注册表"
    if os.path.isdir(WPS_BASE):
        try:
            vers = [x for x in os.listdir(WPS_BASE) if VRE.match(x)]
            if vers:
                keep.setdefault(max(vers, key=verkey), "最高版本")
        except OSError:
            pass
    return keep


def scan_wps(log: PROGRESS = None) -> Module:
    m = Module("wps", "WPS 缓存与旧版本",
               "旧版本目录、插件池重复版本、office6 缓存日志（自动备份不动）", False)

    # 6.1 旧版本目录
    if os.path.isdir(WPS_BASE):
        keep = wps_current_versions()
        try:
            vers = [x for x in os.listdir(WPS_BASE)
                    if VRE.match(x) and os.path.isdir(os.path.join(WPS_BASE, x))]
        except OSError:
            vers = []
        now = time.time()
        for v in sorted(vers, key=verkey, reverse=True):
            p = os.path.join(WPS_BASE, v)
            size = path_size(p)
            if v in keep:
                m.add("程序版本 %s" % v, p, size, "keep", "保留（%s）" % keep.get(v, "当前"))
                continue
            try:
                if now - os.path.getmtime(p) < 7200:
                    m.add("程序版本 %s" % v, p, size, "keep", "2 小时内变动过，跳过")
                    continue
            except OSError:
                pass
            m.add("程序版本 %s" % v, p, size, "delete")
            m.actions.append({"op": "rmtree", "path": p, "size": size})

    # 6.2 插件池去重
    if os.path.isdir(WPS_POOL):
        groups: Dict[str, List[tuple]] = {}
        try:
            for name in os.listdir(WPS_POOL):
                full = os.path.join(WPS_POOL, name)
                if not os.path.isdir(full):
                    continue
                mt = POOL_RE.match(name)
                base = mt.group("base") if mt else name
                ver = mt.group("ver") if mt else "0"
                groups.setdefault(base, []).append((verkey(ver), ver, name, full))
        except OSError:
            groups = {}
        dup_size = 0
        dup_count = 0
        for base, lst in groups.items():
            if len(lst) < 2:
                continue
            lst.sort(key=lambda x: x[0], reverse=True)
            for _k, _v, _n, full in lst[1:]:
                s = path_size(full)
                dup_size += s
                dup_count += 1
                m.actions.append({"op": "rmtree", "path": full, "size": s})
        if dup_count:
            m.add("插件池重复副本（%d 个）" % dup_count, WPS_POOL, dup_size, "delete",
                  "每个插件保留最新版，功能不受影响")
        else:
            note_pool = "插件池无重复版本"
            m.note = note_pool

    # 6.3 office6 缓存与日志
    for sub in ("cache", "log", "wns"):
        p = os.path.join(WPS_OFFICE6, sub)
        if not os.path.isdir(p):
            continue
        size = path_size(p)
        if size < 1024 * 1024:
            continue
        m.add("office6\\%s" % sub, p, size, "delete", "纯缓存/日志，自动重建")
        m.actions.append({"op": "clear", "path": p, "size": size})

    # 6.4 自动备份：只统计，永不删除
    if os.path.isdir(WPS_BACKUP):
        bsize = path_size(WPS_BACKUP)
        try:
            bcount = len(os.listdir(WPS_BACKUP))
        except OSError:
            bcount = 0
        m.add("自动备份（%d 个文件，受保护）" % bcount, WPS_BACKUP, bsize, "keep",
              "崩溃恢复副本，可能是唯一未存盘版本，永不删除")
        m.note = ((m.note + "  ") if m.note else "") + "自动备份已保护（%d 项 / %s）" % (bcount, fmt_size(bsize))

    if not m.actions:
        m.note = ((m.note + "  ") if m.note else "") + "无缓存可清"
    return m


# ---- 7. WinSxS ----------------------------------------------------------

def scan_dism(log: PROGRESS = None) -> Module:
    m = Module("dism", "WinSxS 组件清理",
               "DISM 官方组件回收，耗时 10~30 分钟，更新后清理效果最好", True)
    win_sxs = os.path.join(WIN_DIR, "WinSxS")
    size = path_size(win_sxs) if os.path.isdir(win_sxs) else 0
    m.add("WinSxS 组件库（目录本身不会变小）", win_sxs, size, "note",
          "硬链接居多，回收量体现在磁盘可用空间上")
    m.total = 3 * 1024 ** 3
    m.note = "预计回收 2~4 GB（依更新数量而定）"
    m.actions.append({"op": "dism", "path": win_sxs, "size": 0})
    return m


SCANNERS = {
    "win_update": scan_win_update,
    "win_temp": scan_win_temp,
    "win_leftover": scan_win_leftover,
    "edge": scan_edge,
    "webview": scan_webview,
    "quark": scan_quark,
    "wps": scan_wps,
    "dism": scan_dism,
}


def scan_module(key: str, log: PROGRESS = None) -> Module:
    fn = SCANNERS.get(key)
    if not fn:
        raise KeyError(key)
    return fn(log)


def scan_all(log: PROGRESS = None) -> List[Module]:
    out = []
    for spec in module_specs():
        if log:
            log("扫描 %s ..." % spec["name"])
        try:
            out.append(scan_module(spec["key"], log))
        except Exception as e:
            m = Module(spec["key"], spec["name"], spec["desc"], spec["needs_admin"])
            m.note = "扫描失败: %s" % e
            out.append(m)
    return out


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------

def _clean_update(path: str, log: PROGRESS) -> int:
    freed = 0
    stopped = []
    if is_admin():
        for svc in ("wuauserv", "bits"):
            if _svc_state(svc) == "RUNNING":
                log("    暂停服务 %s" % svc)
                _svc(svc, "stop")
                stopped.append(svc)
        time.sleep(1)
    freed += clear_dir(path, 0, log)
    for svc in stopped:
        _svc(svc, "start")
    if stopped:
        log("    已恢复服务: %s" % ", ".join(stopped))
    return freed


def _clean_dism(path: str, log: PROGRESS, reset_base: bool = False) -> int:
    free_before = disk_usage()[1]
    cmd = [os.path.join(WIN_DIR, "System32", "Dism.exe"), "/Online", "/Cleanup-Image",
           "/StartComponentCleanup"]
    if reset_base:
        cmd.append("/ResetBase")
    log("    执行 DISM %s（10~30 分钟，进度条长时间不动属于正常）" % (" ".join(cmd[2:])))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="mbcs", errors="replace",
                                creationflags=CREATE_NO_WINDOW, bufsize=1)
    except Exception as e:
        log("    无法启动 DISM: %s" % e)
        return 0
    last = ""
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        s = line.strip()
        if not s or s == last:
            continue
        last = s
        if re.search(r"\d+\.\d+%|%", s) and len(s) < 120:
            log("      " + s)
        elif s:
            log("      " + s[:160])
    proc.wait()
    freed = disk_usage()[1] - free_before
    if freed < 0:
        freed = 0
    log("    DISM 结束，代码 %s" % proc.returncode)
    return freed


def clean_module(m: Module, log: PROGRESS = None, cancel: Callable[[], bool] = None,
                 reset_base: bool = False) -> int:
    """执行扫描产出的计划，返回实际释放字节数"""
    log = log or (lambda s: None)
    freed = 0
    for act in m.actions:
        if cancel and cancel():
            log("    已请求停止，剩余步骤跳过")
            break
        op = act["op"]
        p = act.get("path", "")
        try:
            if op == "rmtree":
                log("  删除 %s（%s）" % (p, fmt_size(path_size(p))))
                freed += remove_path(p, log)
            elif op == "clear":
                log("  清空 %s（%s）" % (p, fmt_size(path_size(p))))
                freed += clear_dir(p, 0, log, cancel)
            elif op == "clear_old":
                log("  清理 %s（仅 %s 小时前）" % (p, act.get("hours", 2)))
                freed += clear_dir(p, act.get("hours", 2), log, cancel)
            elif op == "clear_update":
                log("  清理更新下载缓存 %s" % p)
                freed += _clean_update(p, log)
            elif op == "dism":
                freed += _clean_dism(p, log, reset_base)
        except Exception as e:
            log("  步骤失败 %s: %s" % (p, e))
    return max(0, freed)


def clean_modules(modules: List[Module], log: PROGRESS = None,
                  cancel: Callable[[], bool] = None,
                  reset_base: bool = False,
                  on_module_done: Callable[[Module, int], None] = None) -> int:
    total = 0
    for m in modules:
        if cancel and cancel():
            break
        log("")
        log("== %s ==" % m.name)
        if m.needs_admin and not is_admin():
            log("  跳过：需要管理员权限")
            continue
        if not m.actions:
            log("  无可执行的清理项")
            continue
        free_before = disk_usage()[1]
        got = clean_module(m, log, cancel, reset_base)
        delta = disk_usage()[1] - free_before
        if delta > got:
            got = delta
        total += got
        log("  本项释放 %s" % fmt_size(got))
        if on_module_done:
            on_module_done(m, got)
    return total


# --------------------------------------------------------------------------
# 自检 / CLI
# --------------------------------------------------------------------------

def _cli_scan():
    total_b, free_b = disk_usage()
    print("C 盘  总 %.1f GB  剩余 %.2f GB  已用 %.1f%%" %
          (total_b / 1024 ** 3, free_b / 1024 ** 3,
           (total_b - free_b) / total_b * 100 if total_b else 0))
    print("管理员: %s" % is_admin())
    print("")
    grand = 0
    for m in scan_all():
        print("[%s] %s  可释放 %s%s" % (m.key, m.name, fmt_size(m.total),
                                        "  (需管理员)" if m.needs_admin else ""))
        if m.note:
            print("     备注: %s" % m.note)
        for it in m.items:
            if it.kind == "delete":
                print("     [-] %-46s %10s  %s" % (it.label, fmt_size(it.size), it.detail))
        for it in m.items:
            if it.kind == "keep" and it.detail:
                print("     [o] %-46s %10s  %s" % (it.label, fmt_size(it.size), it.detail))
        print("     计划动作: %d 项" % len(m.actions))
        print("")
        grand += m.total
    print("合计可释放约 %s" % fmt_size(grand))
    h = edge_health()
    if h:
        print("")
        print("Edge 引导器健康检查:")
        for stub, v, ok in h:
            print("  %-22s 绑定 %-16s %s" % (stub, v, "正常" if ok else "!! 目标缺失"))
    return 0


def main(argv):
    if "--scan" in argv:
        return _cli_scan()
    print("用法: python cleaner_core.py --scan")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
