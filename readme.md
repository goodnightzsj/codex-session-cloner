# AI CLI Kit (`aik`)

上游仓库：[goodnightzsj/codex-session-cloner](https://github.com/goodnightzsj/codex-session-cloner.git)

`ai-cli-kit` 是一个本地 AI CLI 工具箱。打包了两个子工具，共享同一套底层（原子写入 / 跨进程锁 / TUI 渲染 / Windows VT / UTF-8 launcher）：

| 子工具 | 用途 | 兼容入口 |
|---|---|---|
| **Codex Session Toolkit** | 浏览 / 迁移 / 导入导出 / 修复 Codex 会话 | `cst` / `codex-session-toolkit` |
| **CC Clean (Claude Code)** | 安全清理 Claude 本地标识 / 遥测 / 历史，自动备份 | `cc-clean` |

![界面预览](./assets/12345.png)

命令总览（`aik --help` / `aik claude --help`）：

![CLI 总览](./assets/cli-overview.png)

| `aik` 顶层 hub | CC Clean 子工具 |
|---|---|
| ![aik hub](./assets/aik-hub.png) | ![CC Clean TUI](./assets/cc-clean-tui.png) |

<details>
<summary>更多 CLI 截图（Codex Session Toolkit 命令列表 / CC Clean 可清理目标）</summary>

### `aik codex --help` —— Codex 会话工具箱的全部子命令
![codex --help](./assets/cli-codex-help.png)

### `aik claude list-targets` —— CC Clean 可清理的本地标识 / 遥测 / 缓存 / 历史目标键（清理前自动备份）
![claude list-targets](./assets/cli-claude-targets.png)

</details>

## 30 秒上手

```bash
git clone https://github.com/goodnightzsj/codex-session-cloner.git
cd codex-session-cloner
./install.sh        # macOS / Linux：装好 venv + 注册 aik 命令
# Windows 用户改双击 install.bat
aik                 # 进交互界面：↑↓ 选工具、Enter 确认
```

不想装任何东西？项目目录里直接跑 `./aik` 等效，零污染。**最常见的两个动作**：

- 看本机 Codex 会话：`aik codex list`
- 安全清理 Claude 本地缓存与遥测（自动备份）：`aik claude clean --preset safe --yes`

下面是完整命令清单和进阶用法。

## 选哪种运行方式？

| 方式 | 命令 | 需要安装吗？ | 会在 PATH 里多命令吗？ |
|---|---|---|---|
| **launcher 直跑（最省事）** | `./aik` / `./codex-session-toolkit` / `./cc-clean` | ❌ 不需要 | ❌ 否（项目目录内） |
| **`python -m` 直跑** | `python -m ai_cli_kit[.codex|.claude]` | ✅ 需要（或 `PYTHONPATH=src`） | ❌ 否 |
| **标准安装 + console scripts** | 全局 `aik` / `cst` / `cc-clean` | ✅ `./install.sh` | ✅ venv/bin 注册 4 个命令 |
| **极简安装（不要命令）** | `./install.sh --no-scripts` + `python -m ai_cli_kit` | ✅ 但仅装包 | ❌ 否（脚本被剔除） |

不知道选哪个？**直接 `git clone` 后跑 `./aik`**，零安装、零污染、立即可用。

### 一键安装（macOS / Linux）

```bash
chmod +x install.sh aik cc-clean codex-session-toolkit codex-session-toolkit.command
./install.sh                  # 标准安装：venv/bin 注册 aik / cst / codex-session-toolkit / cc-clean
./install.sh --no-scripts     # 极简安装：装包但不注册任何命令，只能 python -m 用
./install.sh --editable       # 开发模式（pip install -e）
./aik                         # 进入交互菜单
```

`./install.sh --force` 现在会拒绝删除项目根目录，以及任何会把当前仓库一起删掉的祖先目录；`VENV_DIR` 只应用来指向单独的虚拟环境目录。

### 一键安装（Windows）

双击 `install.bat`，再双击 `aik.cmd`。或：

```powershell
.\install.ps1                 # 标准安装
.\install.ps1 -NoScripts      # 极简安装：装包但不注册命令
.\aik.cmd
```

`.\install.ps1 -Force` 同样会拒绝把仓库根目录或其祖先目录当成 `VENV_DIR` 删除。

### `python -m` 直跑（不想注册任何命令）

```bash
# 在项目目录内（无需 pip install）
PYTHONPATH=src python -m ai_cli_kit              # 顶层菜单
PYTHONPATH=src python -m ai_cli_kit.codex        # 直接进 Codex
PYTHONPATH=src python -m ai_cli_kit.claude       # 直接进 CC Clean

# 或者用 make 包裹（自动设 PYTHONPATH）
make run            # 顶层菜单
make run-codex      # Codex 子工具
make run-claude     # CC Clean

# 已 pip install 后（无需 PYTHONPATH，任意目录）
python -m ai_cli_kit
python -m ai_cli_kit.codex
python -m ai_cli_kit.claude
```

### 进 TUI 后

无参运行 `./aik`（或 `python -m ai_cli_kit`）→ 用 ↑↓ 选 **Codex Session Toolkit** 或 **CC Clean** → Enter 进入对应工具的菜单。进入 Codex 工具后，可在 **Repair / Maintenance** 分组里选择 **启动 provider 后台监听**，等价于在后台运行 `watch-provider` 并把日志写入 `~/.codex/provider-watch.log`。

## 常用命令

### Codex（会话管理）

按使用场景选命令：

| 我想…                                | 跑 |
|---|---|
| 看本机有哪些 Codex 会话              | `aik codex list` |
| 导出一个会话为 Bundle                | `aik codex export <session_id>` |
| 批量导出所有 Desktop 会话            | `aik codex export-desktop-all` |
| 导入别人给的 Bundle                  | `aik codex import <session_id>` |
| 切换 provider 后继续聊（克隆会话）   | `aik codex clone-provider` |
| 自动监听 provider 切换并同步克隆     | `aik codex watch-provider` |
| Desktop 列表里某会话不见了           | `aik codex repair-desktop` |
| 归档对话太多想清理（不可恢复）       | `aik codex clean-archived --dry-run` → 加 `--yes` 执行 |

<details>
<summary>完整命令清单（含 dry-run / target_provider 等高级开关）</summary>

```bash
./aik codex list                       # 列出本机 Codex 会话
./aik codex export <session_id>        # 导出单个会话为 Bundle
./aik codex export-desktop-all         # 批量导出 Desktop 会话
./aik codex import <session_id>        # 导入 Bundle
./aik codex clone-provider             # 切换 provider 后克隆
./aik codex watch-provider             # 监听 provider 变化并自动克隆
./aik codex clean-archived --dry-run   # 预览清理已归档对话
./aik codex clean-archived --yes       # 清理已归档对话及其 Desktop 元数据
./aik codex repair-desktop             # 修复 Desktop 可见性 / 索引
./aik codex --help                     # 完整子命令清单
```

</details>

#### `watch-provider` 后台监听

`watch-provider` 会持续读取 `~/.codex/config.toml` 里的 `model_provider`。启动后默认先按当前 provider 同步一次，之后只有检测到 provider 变化时才再次调用现有的 `clone-provider` 逻辑；克隆逻辑本身是幂等的，已同步过的会话会跳过。

常用参数：

```bash
./aik codex watch-provider --interval 2          # 每 2 秒检查一次（默认）
./aik codex watch-provider --no-initial-sync     # 启动时不补同步，只监听后续切换
./aik codex watch-provider --dry-run             # 只预览 provider 变化时会克隆什么
./aik codex watch-provider --once                # 执行一次检查后退出，适合脚本验证
```

macOS / Linux 想挂到后台，可以把日志重定向到 Codex 目录：

```bash
nohup ./aik codex watch-provider >> ~/.codex/provider-watch.log 2>&1 &
```

#### `clean-archived` 清理范围

`clean-archived` 面向 Codex Desktop 已归档对话，适合在侧边栏归档列表已经不再需要保留时释放本地会话数据。建议先退出 Codex Desktop，再执行 dry-run：

```bash
./aik codex clean-archived --dry-run
./aik codex clean-archived --yes
```

执行时会删除：

- `~/.codex/archived_sessions/` 下的归档 rollout JSONL 与对应 `.lock` 文件
- `~/.codex/session_index.jsonl` 中这些归档 session id 的索引项
- 最新 `~/.codex/state_*.sqlite` 中这些归档线程的 Desktop 元数据行（包括 `threads`、`thread_dynamic_tools` 等相关表）
- `.codex-global-state.json` 中这些线程对应的 workspace hints、prompt-history、heartbeat 权限等残留

注意：`clean-archived` 不创建备份，也没有 restore 子命令；真实删除必须显式传 `--yes`。如果某个 session id 同时仍存在于 `~/.codex/sessions/`，工具只删除归档目录里的残留文件，不清理 active 线程的 index / sqlite / global state 元数据。

兼容写法：把 `./aik codex` 换成 `./codex-session-toolkit` 即可，参数完全一致。

### CC Clean（Claude 本地清理）

> **一句话**：`safe` 预设清掉登录痕迹/缓存/遥测但**保留** projects 和 sessions；`full` 全清，**会丢对话历史**。所有删除默认自动备份，可以 restore。

按使用场景选命令：

| 我想…                                 | 跑 |
|---|---|
| 看安全预设到底要删什么（不动磁盘）    | `aik claude plan` |
| 安全清理（清登录痕迹，留对话历史）    | `aik claude clean --preset safe --yes` |
| 完整重置（**含会话数据，慎用**）      | `aik claude clean --preset full --yes` |
| 从备份还原                            | `aik claude restore <backup-path> --yes` |
| 删旧备份只留最近 5 份                 | `aik claude prune-backups --keep 5 --yes` |
| 路径解析诊断                          | `aik claude debug-paths --format json` |

<details>
<summary>完整命令清单（含 remap-history / list-targets）</summary>

```bash
./aik claude plan                              # 预览默认安全清理计划
./aik claude clean --preset safe --yes         # 执行安全清理（自动备份）
./aik claude clean --preset full --yes         # 完整重置（含会话数据，慎用）
./aik claude remap-history --run-claude --yes  # 重新生成新 ID 并回写历史
./aik claude restore <backup-path> --yes       # 从备份目录还原
./aik claude prune-backups --keep 5 --yes      # 清理旧备份目录
./aik claude debug-paths --format json         # 诊断：查看解析后的路径 + env
./aik claude list-targets                      # 列出所有清理目标键名
./aik claude --help
```

兼容写法：`./aik claude` 等价于 `./cc-clean`。

</details>

<details>
<summary>清理覆盖范围（60+ targets 跨 7 类）+ 安全机制 + JSON 模式</summary>

**清理覆盖范围（60+ targets，跨 7 类）**：

```
身份/凭据：state_user_id, state_full_identity, legacy_state_file, credentials_file,
            macos_keychain (含 16 服务名变体 + locked 检测), settings_auth_env
PII / 缓存：telemetry, statsig, paste-cache, dump-prompts, traces, file-history,
            image-cache, stats-cache, startup-perf, usage-data, uploads (bridge)
状态/锁：   plugins, debug, ide, teams, session-env, agent-memory,
            mcp-needs-auth-cache, mcp-refresh-*.lock (per-server),
            computer-use.lock, policy-limits, remote-settings,
            output-styles, completion.{bash,zsh,fish}, workflows
旧备份：   ~/.claude/backups/.claude*.json.{backup,corrupted}.* + 旧 HOME 直下
危险（默认不勾）：projects, history, sessions, user_claude_md, plans, jobs, tasks,
            agents, skills, rules, keybindings, magic-docs, chrome
XDG 出 ~/.claude/：xdg_data_claude / xdg_cache_claude / xdg_state_claude
            （native installer 写到 $XDG_DATA_HOME/claude 等）
环境重定向：CLAUDE_CONFIG_DIR / CLAUDE_COWORK_MEMORY_PATH_OVERRIDE /
            CLAUDE_CODE_PLUGIN_CACHE_DIR / CLAUDE_CODE_REMOTE_MEMORY_DIR /
            CLAUDE_CODE_TMPDIR / XDG_DATA_HOME / XDG_CACHE_HOME / XDG_STATE_HOME
scratchpad: ${TMPDIR}/claude (Windows) / ${TMPDIR}/claude-<uid> (POSIX)
跨 OS：    Windows 长路径 + 保留名 sanitize / NTFS junction 守卫 /
          macOS NFC 路径 / POSIX 0o700 备份目录权限
```

**安全机制**：

- 默认所有删除走 `~/.claude-clean-backups/<时间戳-uuid>/` 备份目录，POSIX 上 0o700 + 内文件 0o600
- `prune-backups` / `restore` / `remap-history` 认定“最新备份”时优先使用备份目录名里的创建时间（以及 sidecar metadata），不会因为旧备份目录后来被 touch / copy 而错把它排到最新
- 备份带 `_cc_clean_meta.json` sidecar 记录原始 anchor，确保 restore 能还原到正确位置
- restore 严格防路径穿越：trusted-anchor whitelist + commonpath 边界 + dst 父链 realpath
- 跨进程文件锁防并发（execute_plan / restore / prune-backups 三入口）
- 异常消息脱敏（不在 JSON 输出中泄露文件路径）
- `--no-backup` 显式关闭备份；`--dry-run` 只预览不动磁盘

**JSON 模式**：所有子命令支持 `--format json` 输出单文档 envelope `{command, status, ...}`，`status` 为 `ok` / `partial` / `error` / `empty`。`--format=json` 模式下未传 `--yes` 且非 `--dry-run` 时拒绝执行（防自动化脚本无意识破坏数据）。

</details>

## 常见问题

<details>
<summary>装完跑 <code>aik</code> 报 <code>command not found</code></summary>

`install.sh` 把命令注册到了 venv/bin，如果 venv/bin 不在 PATH 里就找不到。两种处理：

```bash
# 方案 A：把 venv/bin 加进 PATH（持久）
echo 'export PATH="$HOME/.local/share/aik/venv/bin:$PATH"' >> ~/.bashrc   # zsh 改 ~/.zshrc
source ~/.bashrc

# 方案 B：项目目录里直接跑 launcher，零依赖
./aik
```

</details>

<details>
<summary>没装 Codex Desktop / Claude Code 还能用吗？</summary>

可以。`aik` 只在你触发对应子工具时才读对方的本地数据：

- 没装 Codex → `aik codex` 子命令会报 `Missing file: ~/.codex/...`，但不影响 CC Clean 部分；
- 没装 Claude Code → `aik claude` 同理；
- 顶层菜单 `aik` 始终能进，互不依赖。

</details>

<details>
<summary>误删了能恢复吗？</summary>

- `aik claude clean` **默认自动备份**到 `~/.claude-clean-backups/<时间戳-uuid>/`，跑 `aik claude restore <backup-path> --yes` 即可还原；
- `aik codex export/import` 默认不动原文件，导入回去就行；
- ⚠️ **`aik codex clean-archived --yes` 不创建备份、也没有 restore 子命令**。务必先 `--dry-run` 看清单，确认无误再加 `--yes`。

</details>

<details>
<summary>Windows 上有什么需要特殊准备的吗？</summary>

- 装 Python 3.10+。
- 双击 `install.bat` 即可，工具内部已经处理长路径 + Windows 保留文件名 + NTFS junction 守卫。
- 若 PowerShell 阻止脚本，先在管理员 PowerShell 跑：`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`。

</details>

## 制作发布包

```bash
./release.sh
# 输出 dist/releases/ai-cli-kit-<version>.tar.gz / .zip
```

对方解压后跑 `./install.sh`（macOS / Linux）或 `install.bat`（Windows）即可。

## 工程命令

```bash
make help          # 看所有 target
make bootstrap     # 等价 ./install.sh
make test          # 跑全部单测（需 PYTHONPATH=src）
make check         # compile + test + launcher smoke
make release       # 等价 ./release.sh
```

---

<div align="center">

**学 AI，上 L 站**

[![LINUX DO](https://img.shields.io/badge/LINUX%20DO-社区-gray?style=flat-square)](https://linux.do/)

本项目在 [LINUX DO](https://linux.do/) 社区发布与交流。

</div>

## Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=goodnightzsj/codex-session-cloner&type=Date&theme=dark">
  <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=goodnightzsj/codex-session-cloner&type=Date">
  <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=goodnightzsj/codex-session-cloner&type=Date">
</picture>

## 许可证

MIT License
