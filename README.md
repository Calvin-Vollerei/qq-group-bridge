# QQ群文件搬运工

把指定 QQ 群里的**新文件自动抓取、按规则过滤、并上传到网盘**的 Windows 桌面工具。

界面友好、配置透明、**分发包内不含任何凭据** —— 适合部署在**知情同意**的机器上（例如帮朋友把学习群的资料自动归档到网盘）。

```
QQ 群 ──①枚举/下载──▶ 本工具 ──②WebDAV──▶ OpenList ──③──▶ 百度网盘
                       │
                       └─ 按群号/群名分子目录、断点续传、去重、失败重试
```

程序把文件下到**临时目录**，上传成功后删除 —— 可以做到**本地不留副本**。

---

## 特性

| 能力 | 说明 |
|---|---|
| **自动归档** | 定时轮询群文件，只搬运新出现的（已入库的自动跳过） |
| **全量历史** | 首轮会拉取群里的**全部历史文件**，不漏早期资料 |
| **分门别类** | 按群号 / 群名 / `群号_群名` 分子目录，也可逐群自定义目录名 |
| **规则过滤** | 文件名正则（包含/排除）、扩展名白名单、体积上下限 |
| **可靠传输** | 断点续传、失败重试、上传后回读校验、sha256 记录、去重 |
| **中断可恢复** | 进程被杀后重启，进行中的任务自动退回待处理 |
| **凭据加密** | Windows DPAPI（绑定当前用户+本机），拷到别的电脑即失效 |
| **日志脱敏** | 所有日志经统一过滤器；令牌/cookie/手机号一律掩码 |
| **零外联** | 不向任何第三方服务器上报数据 |

---

## 下载安装（普通用户）

到 [**Releases**](https://github.com/Calvin-Vollerei/qq-group-file-bridge-QQ/releases) 下载
`qgb-v1.0-full.zip`（约 52 MB），解压到任意目录，双击 `启动搬运工.bat` 即可。

这个整包 = 主程序（自带 Python 3.11 运行时，**不需要装 Python**）+ NapCat 组件
加载器。压缩包内**不含**任何凭据、群号、网盘账号，也**不含腾讯客户端二进制** ——
QQ 运行时会在你首次点「启动组件」时由 `NapCatInstaller.exe` 从官方渠道自行下载。

> 想自己装配？见下面的「快速开始（源码运行）」。
> 只想下载主程序、NapCat 另外装？用 `qgb-v1.0-app-only.zip`。

### 三步上手

1. 解压后双击 `启动搬运工.bat`；首次启动 Windows 防火墙若弹窗，选「允许」
2. **QQ 登录** 页 →「启动组件」→ 等就绪 →「获取二维码」→ 手机 QQ 扫码
3. **网盘与凭据** 页 → 填 WebDAV 地址与账号 →「测试连接」→ 回 **监控** 页
   点「▶ 开始监控」

> NapCat 是非官方 QQ 客户端，**请用专用小号**，并保持默认轮询间隔。
> 详见下方「风险提示与免责声明」。

---

## 快速开始

### 环境要求

* Windows 10/11 x64
* Python 3.10+（仅从源码运行时需要；发布包已含运行时）
* 一个**专用 QQ 小号**（见下方「风险提示」）

### 1. 安装依赖

```bat
pip install -r requirements.txt
```

### 2. 准备 QQ 接入组件（NapCat）

本工具通过 [NapCat](https://github.com/NapNeko/NapCatQQ) 提供的 OneBot 11 HTTP 接口读写群文件。
**本项目不捆绑、不再分发任何腾讯客户端二进制**，需要你自己下载。

推荐用官方**一键无头绿色版**（自带安装器，最省事）：

1. 从 [NapCat Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载
   `NapCat.Shell.Windows.OneKey.zip`（约 1 MB）到任意目录（**不用解压**）
2. 启动本程序 →「QQ 登录」页 → 点 **「从压缩包安装…」**，选中刚下载的 zip
3. 点「启动组件」→ 首次运行会由 `NapCatInstaller.exe` 自动下载 QQ 运行时 → 
   「获取二维码」→ 手机 QQ 扫码

> 这一步也可以命令行做（会顺带预置好 OneBot HTTP 配置，省掉在 NapCat 网页端
> 手点「新建 HTTP 服务器」）：
>
> ```bat
> python scripts\setup_napcat.py --zip NapCat.Shell.Windows.OneKey.zip
> ```
>
> 也可以把 `NapCat.Shell.zip` 解到 `data/napcat/shell/`，
> 由本工具**挂钩**你已安装的 QQ NT —— 但那种方式**需要管理员权限**，
> 且受 QQ 版本限制（见「疑难排查」）。

### 3. 准备网盘中转（OpenList）

推荐 [OpenList](https://github.com/OpenListTeam/OpenList)（AList 的继任者）：

1. 下载 `openlist-windows-amd64.zip` 解压到 `OpenList/`（与本程序同级或程序目录下）
2. 双击 `启动OpenList.bat`（本程序也会在启动时**自动拉起**它）
3. 浏览器打开 `http://127.0.0.1:5244` 登录（首次运行管理员密码见启动日志，
   或用 `openlist.exe admin set 新密码` 设置）
4. 「存储 → 添加」→ 驱动选 **百度网盘** → 勾选 **Use online api** → 
   从 <https://api.oplist.org/> 获取 refresh token 并粘贴 → 保存
5. 确认挂载路径（例如 `/baidu`），本程序里的「远端根目录」要写成
   `/baidu/你的子目录`

> ⚠️ OpenList 的百度驱动有 **单文件 2GB 上限**。若群里有更大的文件，
> 请在「群与规则」页把「最大体积」设成 1900 MB —— 否则会白下载一遍才失败。

### 4. 运行

```bat
python run_bridge.py
```

或直接用发布包里的 `启动搬运工.bat`。

然后在界面里：填群号 → 选上传方式 → 填 WebDAV 地址与账号 → 「测试连接」→「开始监控」。

---

## 界面

| 标签页 | 用途 |
|---|---|
| **监控** | 开始/暂停、实时进度、日志、**立即刷新**、重试失败项、搬运记录 |
| **群与规则** | 群号列表、网盘目录名、文件名过滤、扩展名/体积限制、规则试跑 |
| **QQ 登录** | 组件安装/启动、扫码登录、状态诊断 |
| **网盘与凭据** | 上传方式、OpenList 状态、网盘客户端一键打开、凭据管理 |
| **高级** | 轮询间隔、并发、临时目录、诊断信息 |

---

## 安全设计

1. **分发包零凭据** —— 仓库与发布包里不存在任何 token / 账号 / app_secret
2. **凭据加密落盘** —— Windows DPAPI（`CryptProtectData`），绑定「当前用户 + 本机」
3. **严禁命令行传密** —— 凭据不走命令行参数、不走明文环境变量
4. **日志全脱敏** —— 统一过滤器处理，token / cookie / 手机号一律掩码
5. **不采集账号信息** —— UI、日志中均不展示昵称、会员状态、容量配额
6. **零外联** —— 不向任何第三方服务器上报数据
7. **发布前扫密** —— `scripts/scan_secrets.py` 命中敏感串即中止发布；
   可分发包由 `scripts/make_release_zip.py` 按白名单生成并**扫描该 zip 本身**

---

## 疑难排查

> 以下每一条都是**真机踩出来的**，症状都很有迷惑性。

### QQ / NapCat

**`spawn EPERM` 或 `node: bad option: --no-sandbox`**
NapCat 默认会 fork worker 子进程，走 Windows 命名管道 IPC，并给 worker 传
Electron 专有的 `--no-sandbox`。在受限环境（沙箱、部分安全软件、精简系统）
下这两点都会导致启动失败。
→ 本工具默认以**单进程模式**启动（`NAPCAT_DISABLE_MULTIPROCESSING=1`）。

**挂钩模式必须管理员权限**
NapCat 要把 Hook DLL 注入 QQ 进程，Windows 要求管理员；否则**静默失败**
（界面毫无反应、没有任何报错）。本工具会提前检测并明确拒绝。

**`PacketBackend 不支持当前QQ版本架构：9.9.33-xxxxx`**
NapCat 的 PacketBackend 是**按 QQ 版本定制的**。QQ 比 NapCat 新时会不支持，
表现为**能列文件、但取不到下载直链**（`packetBackend不可用`）。
→ 用 NapCat **自带运行时**的包（它内含 NapCat 支持的那个 QQ 版本），
不要挂钩自己刚装的 QQ。

**`The specified module could not be found`（wrapper.node）**
`NapCat.Shell.Windows.Node.zip` 里的 `wrapper.node` 依赖 `crypto.dll` 与
`ssl.dll`，而这两个文件**不在包里**（来自 QQ NT 客户端）。
→ 从 QQ NT 的 `versions/<版本>/resources/app/` 复制过来；
本工具的 `scripts/pe_deps.py` 可以定位缺失的依赖。

### OneBot 接口

**`不支持的Api get_group_file_list`**
NapCat 里**没有**这个动作名。正确的是：

| 用途 | 动作名 |
|---|---|
| 根目录 | `get_group_root_files` |
| 子文件夹 | `get_group_files_by_folder` |
| 下载直链 | `get_group_file_url` |

**只能看到最近几十个文件？**
`get_group_root_files` **默认只返回约 40–50 条**，必须显式传 `file_count`
才能拿到全部（服务端上限约 1100，本工具默认传 5000）。
不传就会**静默丢掉历史文件**。

### OpenList / 网盘

**WebDAV 报 403，但网页端和 API 都正常**
OpenList 的 WebDAV 对 `PUT`/`MKCOL` **单独校验权限位**
（`CanWebdavManage`，即 permission 的 **bit 9 = 512**）。
缺这一位时网页端能读写、**只有 WebDAV 报 403**，且响应体为空、日志里没有任何原因。
→ 到 OpenList「用户」里编辑账号，勾上「WebDAV 管理」权限。

**`empty token returned from official API`**
百度网盘 **只保留最新一个** refresh token：多次点「获取 Token」后，
只有最后一次的有效。
→ 回 <https://api.oplist.org/> 重新获取，复制后**立刻**粘贴保存，中间不要重复点击。

**`invalid refresh token, lifetime changed`**
同上，或令牌不是通过 `api.oplist.org` 拿的（那用的是别的 app 凭据，无法刷新）。
用 `python scripts/test_baidu_token.py` 可以直接看到原始回应。

---

## 开发

```bat
rem 单元测试（314 项）
python -m unittest discover -s tests -t .

rem 离线端到端冒烟（无需任何真实账号）
python -m qgb.dev.smoke

rem 凭据链路验证（真机 DPAPI）
python scripts/verify_dpapi.py

rem GUI 结构与渲染检查
python scripts/gui_inspect.py
python scripts/gui_smoke.py

rem 发布前扫密
python scripts/scan_secrets.py --source

rem 打包（含测试/冒烟/扫密闸门 + 生成可分发包）
powershell -File scripts/build.ps1

rem 合成 GitHub 发布用整包（主程序 + NapCat 组件，自带自检与扫密）
python scripts/pack_release.py --version v1.0
```

CI（GitHub Actions，`.github/workflows/ci.yml`）在 Python 3.10/3.11/3.12 上
跑单元测试 + 离线冒烟 + 源码扫密，**全程不接触任何真实账号与密钥**。

### 目录结构

```
qgb/
  config.py        配置模型与校验
  paths.py         数据目录解析（含写入探针与便携模式）
  secrets.py       DPAPI 加密凭据库
  redact.py        日志脱敏
  store.py         SQLite 状态库（去重/断点/记录）
  models.py        数据模型
  filters.py       规则引擎
  downloader.py    下载器（断点续传/重试）
  naming.py        网盘目录命名（净化/风格/冲突检测）
  openlist.py      OpenList 进程托管（自动启停）
  netdisk_client.py 网盘客户端自动探测与打开
  pipeline.py      搬运流水线（核心编排）
  controller.py    门面层（GUI 的纯逻辑接口，可无头测试）
  napcat/          OneBot 客户端 / 进程托管 / 二维码
  uploaders/       WebDAV 与本地目录适配器
  gui/             tkinter 界面
  dev/             测试替身与冒烟（**不随发布包分发**）
scripts/           构建、打包、验证、扫密、部署辅助
tests/             314 项单元测试
packaging/         发布包内的脚本与说明
```

**设计约定**：`AppController` 是不依赖 tkinter 的门面层，
界面只是它的一个视图 —— 因此全部业务逻辑都能在无 GUI 环境下测试。

### 发布流程

1. `powershell -File scripts/build.ps1` —— 跑全部闸门并生成 `dist/QQ群文件搬运工/`
   与 `dist/qgb-v1.0-app-only.zip`（**不含** `data/`，即不含任何凭据）
2. 在该目录里完成一次真实登录（NapCat 组件才会就位），再
   `python scripts/pack_release.py --version v1.0` 合成整包
3. 把 `dist/qgb-v1.0-full.zip` 拖进 GitHub 的 Release

第 2 步的整包脚本会自动排除本机运行痕迹：NapCat 跑过之后会在
`data/napcat/shell/` 里留下 `napcat.out.log`、`guild1.db`、`cache/qrcode.png`
等**含 QQ 号与群号**的文件，脚本按目录与后缀整体排除，并在打包后重新打开
zip 逐条自检（命中即失败退出），最后再交给 `scan_secrets.py` 扫一遍。

---

## 风险提示与免责声明

* **QQ 风控**：NapCat 属于非官方客户端，使用自动化接口**存在账号被限制的风险**。
  请务必使用**专用小号**，并保持保守的轮询间隔（默认 300 秒、单线程下载）。
* **群文件版权与授权**：搬运他人上传的文件前，请确认你**有权这样做**
  （群主/管理员身份，或已获得授权）。请勿用于传播侵权内容。
* **不要在公网暴露 OpenList**：WebDAV 使用 HTTP Basic 认证，
  明文 HTTP 会让密码在公网裸奔。若需远程访问，请用 HTTPS + 仅放行你的 IP。
* 本项目仅供**学习与个人资料管理**使用。使用者需自行承担因使用本工具产生的
  一切后果，作者不对账号受限、数据丢失或任何间接损失负责。

---

## 许可证

[MIT](LICENSE)
