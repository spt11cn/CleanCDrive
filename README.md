# CleanCDrive — Windows C 盘清理工具

一个带图形界面的 Windows 磁盘清理工具。核心设计原则：**扫描永远只读，删除前必须确认，判定逻辑宁可少删不可错删。**

不是又一个"一键清理"——它只处理有明确归属的残留文件，并且对每一个删除目标都做了来源交叉验证。

![界面预览](CleanCDrive/preview-admin.png)

## 下载即用（推荐）

到 [Releases](../../releases) 页下载 `CleanCDrive.exe`，放到任意目录双击运行。**不需要安装 Python**，单文件约 11 MB，不写注册表，删掉即卸载。

> 单文件 exe 首次启动会先解压到临时目录，约 1~2 秒；杀软偶尔会误报 PyInstaller 打包的程序，属已知现象，可加白名单。

## 从源码运行

需要 Python 3.8+ 且带 tkinter（Windows 官方安装包默认自带）。

```
双击「启动 C盘清理工具.cmd」
```

启动器会依次尝试 `pyw` / `py` / PATH 上的 `pythonw` / 常见安装位置 / 注册表登记的路径，并且**对每个候选实际 `import tkinter` 验证一遍**——因为有些 Python 发行版不带 tkinter，直接调 `pythonw` 会静默失败、什么都不弹。全部失败时会给出明确的下载提示。

也可以手动执行：

```bat
python CleanCDrive.pyw
```

## 权限

界面顶部有黄色提示条时点「以管理员身份重启」，会弹 UAC 确认。只有管理员权限下才能处理 `Program Files` 和 `Windows` 下的目标（小米组件 / Edge / WebView2 / 夸克 / 更新缓存 / WinSxS）。

### 只扫描不删除

```bat
python cleaner_core.py --scan
```

只打印清单，不做任何修改。这一条在 exe 版里没有对应入口，需要源码运行。

## 它清理什么

10 个独立模块，每一项都先扫描出**真实占用**再让你勾选：

| 模块 | 内容 | 需要管理员 |
|---|---|---|
| 小米管家 MiService 旧版本 | 管家升级遗留的版本目录，保留系统服务正在运行的那个 | 是 |
| 小米组件其他残留 | AI 服务旧版本、旧版管家目录、云更新器安装包 | 是 |
| Windows 更新缓存 | 已安装完毕的更新包残留（`SoftwareDistribution\Download`） | 是 |
| 系统临时文件 | `Windows\Temp` 中超过 2 小时的文件 | 是 |
| 升级/回滚残留 | `$WinREAgent`、`$SysReset`、`$Windows.~BT` 等 | 是 |
| Edge 旧版本残留 | 升级后遗留的旧版本目录（按 SxS 绑定判定，见下） | 是 |
| WebView2 旧运行时 | 旧版 WebView2 运行时，默认**不勾选** | 是 |
| 夸克旧版本残留 | 多版本并存时删除废弃版本 | 是 |
| WPS 缓存与旧版本 | 旧版本目录 + 插件池重复版本 + 缓存日志 | 否 |
| WinSxS 组件清理 | DISM 官方组件回收，约 10~30 分钟 | 是 |

## 安全设计

这些规则不是在文档里写的承诺，而是代码里实际拦截的逻辑。

### 1. Edge 旧版本判定不看版本号高低

这是最容易踩的坑。`Microsoft\Edge\Application\msedge.exe` 并不是"启动最高版本目录"，而是通过 Side-by-Side 清单**硬绑定**某一个具体版本目录：

```
msedge.exe → <assemblyIdentity name="157.0.4341.42" version="157.0.4341.42"/>
```

如果那个版本目录被当成"旧版本残留"删掉，Edge 会永久无法启动，而且**更新流程也无法自我修复**——因为新引导器 `new_msedge.exe` 的替换动作需要旧引导器能跑起来才会触发。

所以 `cleaner_core.py` 的做法是：解析每个引导器内嵌的 SxS 清单，读出它绑定的版本号，然后保护三个来源的并集：

- 所有引导器（`msedge.exe` / `new_msedge.exe` / `msedge_proxy.exe` / `new_msedge_proxy.exe` / `pwahelper.exe` / `new_pwahelper.exe`）绑定的版本
- `User Data\Last Version` 记录的当前运行版本
- 每个版本目录内的最高版本

删除完成后还会跑一次健康检查：逐个校验引导器的绑定目标是否仍然存在，缺失会直接在日志里告警。

### 2. WPS 自动备份永不进入删除计划

`office6\backup` 是崩溃恢复副本，里面可能是你唯一没存盘的版本。代码里对它只做大小统计，不做任何删除操作。

### 3. 夸克的保留版本从四个来源交叉验证

`Last Version` 文件、注册表 `DisplayVersion`、根目录主程序 FileVersion、运行进程的路径——任何一个来源都可能过期或错误（实测遇到过注册表写着 7.5.0 但实际运行 7.4.7 的情况），所以取并集保护。

### 4. 小米组件的"当前版本"按真实归属判定

小米全家桶的版本堆积比浏览器更严重（实测一台机器上 `MiService` 堆了 22 个版本、共 8 GB），但三处目录的归属依据并不相同，不能统一按"版本号最高"处理：

- `MiService` / `XiaomiPCManager` 有系统服务，读服务 `ImagePath` 指向哪个版本目录，就保留哪个
- `AIService` 没有服务，读注册表 `DisplayVersion`，并把目录内最高版本一并保护
- `xiaomicloud-updater` 里是安装包缓存，只处理 90 天以前的，避免删掉正在进行的更新

服务信息读不到时会退回"保留最高版本"并在备注里标注，不会按顺序盲删。

### 5. 2 小时内变动过的版本目录一律跳过

防止刚升级完成、还没走完切换流程的版本被误删。

### 6. 不杀进程

相关程序正在运行时，被占用的文件自动跳过，不会强制关闭你正在使用的软件。

## 界面与高分屏

窗口尺寸不写死。程序启动时先按系统 DPI 算出缩放系数（100% / 125% / 150% / 200% / 250% 分别对应 1.0 / 1.25 / 1.5 / 2.0 / 2.5），再把设计尺寸乘上去，上限取屏幕的 94%，因此窗口不会超出屏幕。

列表每行按**实际字宽**排：先用 `tkfont.measure` 量出最长模块名、最长描述的像素宽度，据此确定各列宽度，并据此定出窗口最小宽度。字号会随缩放变大，像素常量同步放大，两者不会错位——早期版本把名字框写死 170 像素，在 200% 缩放下名字和描述就会叠在一起。

勾选框是 Canvas 自绘的：Tk 原生指示器不跟随 DPI，200% 下只剩半个方框。

只有窗口被拖得比最小宽度还窄时，描述才会用省略号收尾，鼠标停在上面会弹出完整文字。屏幕特别窄时，「DISM ResetBase（之后无法卸载更新）」会自动缩成「DISM ResetBase」，避免被挤出窗口。

## 文件说明

```
CleanCDrive/
├── cleaner_core.py           清理引擎：所有扫描、判定、删除逻辑
├── CleanCDrive.pyw           tkinter 图形界面
├── CleanCDrive.spec          PyInstaller 打包配置
├── 启动 C盘清理工具.cmd       源码运行的双击入口
├── app.ico                   图标（同时用于 exe 图标）
├── README.txt                面向使用者的说明
├── preview-user.png          普通权限界面
└── preview-admin.png         管理员权限界面
```

`cleaner_core.py` 不依赖 GUI，可以单独 import 或用 `--scan` 当命令行工具跑。

**日志位置**：源码运行时写在程序目录下的 `cleanup_log.txt`；exe 版写在 `%LOCALAPPDATA%\CleanCDrive\cleanup_log.txt`（因为单文件 exe 的临时解压目录会被清掉，写在里面等于没写）。崩溃记录 `crash.log` 同理。

### 重新打包 exe

```bat
python -m PyInstaller --noconfirm --clean CleanCDrive.spec
```

`CleanCDrive.spec` 里排除了 `PIL`、`numpy`、`tkinter.test` 等只在开发期用到的依赖，能让成品从 17 MB 回到 11 MB。

## 已知限制

- **未做系统还原点自动创建**。动手前建议自行创建还原点。
- **WinSxS 清理默认不启用 ResetBase**。启用后能多回收 1~2 GB，但会失去卸载已安装更新的能力。
- **依赖目录结构的假设**。Edge / 夸克 / WPS / 小米组件的内部布局可能随版本变化，判定逻辑基于当前观测到的结构；遇到未匹配的结构会保守跳过而不是报错删除。
- **小米模块按 `Program Files (x86)\Timi Personal Computing`、`Program Files\MI` 的现有布局编写**，其它品牌或改版后的目录布局需要自行调整。
- **页面文件不在处理范围内**。`pagefile.sys` 由系统自动管理，它的涨落会让可用空间来回波动，这不是清理没生效。
- **仅测试于 Windows 11 Build 26300（2880×1800 @200%）**。

## License

MIT
