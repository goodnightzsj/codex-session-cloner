# Codex Session Toolkit

这是一个面向 Codex 会话管理的工程化工具箱，不再是“根目录直接跑几个 `.py` 脚本”的形态。

它以原来的 `codex-session-cloner` 为产品入口，把 `codex_sessions` 里的核心能力整合进统一的 TUI + CLI 体系，覆盖这三条主线：

- Provider Clone：切换 provider 后继续复用历史会话
- Bundle Transfer：跨机器导出 / 导入会话
- Desktop Repair：修复 Codex Desktop 左侧线程不可见问题

现在的源码已经收进标准 Python 包结构：

- `src/codex_session_cloner/cli.py`
  统一命令入口，负责兼容旧参数、分发 TUI / CLI
- `src/codex_session_cloner/core.py`
  clone / cleanup / bundle transfer / desktop repair 的核心逻辑
- `src/codex_session_cloner/tui_app.py`
  TUI 信息架构、菜单编排、浏览器流程和交互动作
- `src/codex_session_cloner/terminal_ui.py`
  终端渲染、布局、键盘输入和样式基础设施
- `src/codex_session_cloner/__main__.py`
  支持 `python -m codex_session_cloner`
- `scripts/install/`
  安装器的内部实现，根目录 `install.*` 只是对外入口包装
- `scripts/release/`
  发布构建脚本与 release manifest
- `scripts/compat/`
  旧入口兼容层，已经从根目录收纳出去
- `pyproject.toml`
  打包配置，支持安装后直接运行 `codex-session-cloner`

## 核心能力

- 自动识别当前 `model_provider`
- 幂等 clone，不覆盖原始 session
- Dry-run 预演
- 清理旧版无标记 clone
- 浏览本机会话
- 浏览 Bundle 仓库
- 导出单个会话为 Bundle
- 批量导出全部 Desktop 会话为 Bundle
- 批量导出全部 Active Desktop 会话为 Bundle
- 批量导出全部 CLI 会话为 Bundle
- 导入单个 Bundle 为会话
- 批量导入全部 Desktop Bundle 为会话
- 修复 Desktop 可见性
- 自动修复 / 重建 `session_index.jsonl`
- 自动 upsert `state_*.sqlite` 的 `threads` 表
- 自动补充 Desktop workspace roots

## 安装与启动

### 推荐方式：下载后直接部署

现在这个仓库已经提供了项目内安装脚本。下载项目后，不需要自己手敲一串 `pip` 命令，直接执行安装脚本即可。

安装脚本会做这些事：

- 在项目根目录创建本地 `.venv/`
- 把当前项目安装到这个本地环境里
- 保留一个正式产品名 launcher
- 安装完成后可以直接运行工具

macOS / Linux:

```bash
chmod +x ./install.sh ./install.command ./codex-session-cloner ./codex-session-cloner.command
./install.sh
./codex-session-cloner
```

macOS 也可以直接双击：

- `install.command`
- `codex-session-cloner.command`

Windows:

- 双击 `install.bat`
- 或运行：

```powershell
.\install.ps1
.\codex-session-cloner.cmd
```

安装完成后，这几个入口都可以用：

- macOS / Linux：

```bash
./codex-session-cloner
./codex-session-cloner.command
./.venv/bin/codex-session-cloner
```

- Windows：

```powershell
.\codex-session-cloner.cmd
.\.venv\Scripts\codex-session-cloner.exe
```

查看当前版本：

```bash
./codex-session-cloner --version
```

### 开发模式：不安装也可直接运行

如果你是在仓库里继续改代码，也可以不先安装，直接通过仓库 launcher 启动。

macOS / Linux:

```bash
./codex-session-cloner
./codex-session-cloner.command
```

Windows:

```powershell
.\codex-session-cloner.ps1
```

这时它会优先检查本地 `.venv` 里有没有已安装版本；如果还没安装，就自动回退到源码模式，从 `src/codex_session_cloner/` 直接启动。

### 生成可分发压缩包

如果你想把当前仓库直接打成一个可发给别人的安装包，可以运行：

```bash
./release.sh
```

或者：

```bash
make release
```

它会在 `./dist/releases/` 下生成：

- 一个干净的发布目录
- 一个 `.tar.gz`
- 如果系统有 `zip`，再额外生成一个 `.zip`

上传到 GitHub Release 时，直接上传这两个文件即可：

- `./dist/releases/codex-session-cloner-<version>.tar.gz`
- `./dist/releases/codex-session-cloner-<version>.zip`

对方解压后，直接运行：

- macOS / Linux：`./install.sh`
- Windows：`.\install.ps1` 或双击 `install.bat`

release 只会携带分发所需文件；CI、测试、兼容层、release 构建器本身和本地缓存都不会进入发布包。

### 直接安装到当前 Python 环境

如果你就是想装进自己当前的 Python 环境，也仍然支持标准安装方式：

macOS / Linux:

```bash
python3 -m pip install -e .
codex-session-cloner
```

Windows:

```powershell
py -3 -m pip install -e .
codex-session-cloner
```

也支持模块方式：

```bash
python3 -m codex_session_cloner
```

### 用工程命令管理本地开发

如果你想把这个仓库当成一个长期维护的项目来用，而不是临时脚本，可以直接用顶层 [Makefile](/Users/lyston/PycharmProjects/codex-session-cloner/Makefile)：

```bash
make help
make bootstrap
make bootstrap-editable
make release
make run
make install
make test
make smoke
make check
```

## TUI 使用方式

在交互终端里无参数启动，会进入统一 TUI。

主菜单分为 3 个功能域：

1. `Provider / Clone`
2. `Browse / Bundle`
3. `Desktop Repair`

当前交互方式是两级结构：

- 首页先选择功能域
- 回车进入该功能页
- 在功能页中选择具体动作再执行

常用按键：

- `↑/↓` 或 `j/k`：移动
- `Enter`：进入功能页或执行动作
- `←/→`：切换上一页 / 下一页功能页
- `PgUp/PgDn`：功能页切换
- `h`：帮助
- `q`：返回或退出
- `0`：直接退出

浏览器相关按键：

- `/`：过滤会话 / Bundle
- `d`：查看详情
- `e`：在会话列表中直接导出为 Bundle
- `c`：在会话列表中直接克隆
- `t`：在会话列表中直接模拟克隆
- `s`：切换 Bundle 来源过滤
- `i`：导入当前 Bundle 为会话
- `v`：导入当前 Bundle 为会话并自动创建缺失目录

## CLI 用法

### 兼容原 cloner 的入口参数

直接 clone：

```bash
codex-session-cloner
```

Dry-run：

```bash
codex-session-cloner --dry-run
```

清理旧版无标记 clone：

```bash
codex-session-cloner --clean
```

跳过 TUI，直接执行 clone：

```bash
codex-session-cloner --no-tui
```

查看版本：

```bash
codex-session-cloner --version
```

### Canonical 子命令

Provider / Clone:

```bash
codex-session-cloner clone-provider
codex-session-cloner clone-provider --dry-run
codex-session-cloner clean-clones
codex-session-cloner clean-clones --dry-run
```

浏览本机会话：

```bash
codex-session-cloner list
codex-session-cloner list desktop
codex-session-cloner list 019d58
```

浏览 Bundle 仓库：

```bash
codex-session-cloner list-bundles
codex-session-cloner list-bundles --source desktop
codex-session-cloner list-bundles 019d58
```

校验 Bundle 仓库：

```bash
codex-session-cloner validate-bundles
codex-session-cloner validate-bundles --source desktop
codex-session-cloner validate-bundles --source desktop --verbose
```

导出单个会话为 Bundle：

```bash
codex-session-cloner export <session_id>
```

批量导出 Desktop 会话为 Bundle：

```bash
codex-session-cloner export-desktop-all
codex-session-cloner export-desktop-all --dry-run
codex-session-cloner export-active-desktop-all
codex-session-cloner export-active-desktop-all --dry-run
```

兼容旧写法：

```bash
codex-session-cloner export-desktop-all --active-only
```

批量导出 CLI 会话为 Bundle：

```bash
codex-session-cloner export-cli-all
codex-session-cloner export-cli-all --dry-run
```

导入单个 Bundle 为会话：

```bash
codex-session-cloner import <session_id>
codex-session-cloner import ./codex_sessions/bundles/single_exports/<timestamp>/<session_id>
codex-session-cloner import --desktop-visible <session_id>
```

批量导入全部 Desktop Bundle 为会话：

```bash
codex-session-cloner import-desktop-all
codex-session-cloner import-desktop-all --desktop-visible
```

修复 Desktop 可见性：

```bash
codex-session-cloner repair-desktop
codex-session-cloner repair-desktop --dry-run
codex-session-cloner repair-desktop --include-cli
codex-session-cloner repair-desktop --include-cli --dry-run
```

## Bundle 目录策略

所有 Bundle 相关动作都只允许在当前目录下的 `./codex_sessions/` 中进行。

这包括：

- 导出
- 浏览
- 校验
- 导入

不再提供用户可自定义的 `--bundle-root`。

如果你手动传入一个 Bundle 目录，这个目录也必须位于 `./codex_sessions/` 下面，否则工具会拒绝执行。

默认目录：

- Codex 数据目录：`~/.codex/`
- 普通 Bundle 根目录：`./codex_sessions/bundles/`
- Desktop Bundle 根目录：`./codex_sessions/desktop_bundles/`

默认归档结构：

- `./codex_sessions/bundles/single_exports/<timestamp>/<session_id>/`
- `./codex_sessions/bundles/cli_batches/<timestamp>/<session_id>/`
- `./codex_sessions/desktop_bundles/desktop_all_batches/<timestamp>/<session_id>/`
- `./codex_sessions/desktop_bundles/desktop_active_batches/<timestamp>/<session_id>/`

Bundle 内默认包含：

- `codex/<relative rollout path>.jsonl`
- `history.jsonl`
- `manifest.env`

## 三条能力主线

### 1. Provider Clone

适用场景：

- 切换 provider / API 账号后继续 `resume`
- 保留原始 session，不直接改原数据

核心机制：

1. 扫描活动 session 并建立 clone 血缘索引
2. 只处理非当前 provider 的源会话
3. 生成新的 session UUID
4. 改写 metadata 中的 `model_provider`
5. 写入 `cloned_from`、`original_provider`、`clone_timestamp`
6. 输出到新 rollout 文件，不覆盖原 session

### 2. Bundle Transfer

适用场景：

- 跨机器迁移会话
- 把 CLI 会话迁入 Desktop
- 批量归档 / 备份会话

导出流程：

1. 定位 session rollout 文件
2. 提取对应的 `history.jsonl`
3. 校验 session JSONL / history JSONL
4. 生成 `manifest.env`
5. 先写临时目录，校验通过后再原子替换正式 Bundle

导入流程：

1. 解析并白名单校验 `manifest.env`
2. 校验 Bundle 路径安全
3. 校验 session / history JSONL
4. 必要时把 CLI 会话改写成 Desktop 兼容 metadata
5. 对齐目标机当前 `model_provider`
6. 复制 rollout 文件
7. 追加缺失的 history 行
8. 修复 / 重建 `session_index.jsonl`
9. upsert Desktop `threads` 表
10. 自动注册 workspace roots

### 3. Desktop Repair

适用场景：

- Codex Desktop 左侧线程不显示
- provider 不一致
- `session_index.jsonl` 或 `threads` 表损坏 / 缺失
- workspace roots 没登记完整

`repair-desktop` 会执行：

- 将 Desktop 会话的 `model_provider` 对齐到当前 `~/.codex/config.toml`
- 可选把 CLI 会话转换成 Desktop 兼容元数据
- 重新扫描有效 session，重建 `session_index.jsonl`
- 扩展 `.codex-global-state.json` 中保存的 workspace roots
- upsert `state_*.sqlite` 的 `threads` 表

默认备份目录：

- `~/.codex/repair_backups/visibility-时间戳/`

## 安全性说明

- 不修改对话正文内容
- 不会悄悄覆盖原始 session
- 清理操作只针对旧版无标记 clone
- 导入前会校验 manifest、路径和 JSONL
- 建议所有写入型动作第一次都先用 dry-run

## 运行环境

- Python >= 3.8
- 无第三方运行时依赖
- 支持 Windows / macOS / Linux

## 终端环境变量

- `NO_COLOR=1`
- `CSC_ASCII_UI=1`
- `CSC_TUI_MAX_WIDTH=120`

## 现在这个项目的封装状态

和之前相比，当前仓库已经有这几个关键变化：

- 根目录不再摆放核心业务 `.py` 文件
- 代码收敛进 `src/codex_session_cloner/`
- 可以通过 `pyproject.toml` 安装为命令行程序
- 版本号现在由包源码单点维护，CLI 可直接 `--version`
- 项目内已有 `install.sh` / `install.ps1` / `install.bat` 一键部署脚本
- macOS 已补上 `.command` 双击入口
- 主启动器已经统一成产品命名：`codex-session-cloner`
- 内部脚本已经收纳进 `scripts/install`、`scripts/release`、`scripts/compat`
- 兼容层 `csc-launcher.*` 仍保留，但已经移出根目录
- 仓库内可直接执行 `./codex-session-cloner` 或 `.\codex-session-cloner.cmd`
- 可以直接生成分发包：`./release.sh` / `make release`
- GitHub 源码归档也已通过 `.gitattributes` 做了瘦身
- `.venv`、`dist`、测试缓存、coverage 等本地产物都已加入 git ignore
- 已补上基础 smoke tests，可用 `make test` / `python3 -m unittest`
- 已补上 CI workflow，后续改动会自动校验打包、launcher 和安装脚本
- TUI / CLI / README 的命令表述已经统一到 `codex-session-cloner`

如果你要真人测试，最直接的方式就是：

macOS / Linux:

```bash
./install.sh
./codex-session-cloner
```

Windows:

```powershell
.\install.ps1
.\codex-session-cloner.cmd
```

如果你要把它当成正式工具长期使用，推荐先执行一次：

```bash
./install.sh
```

之后直接运行：

```bash
./codex-session-cloner
```
