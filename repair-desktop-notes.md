# repair-desktop 工作原理笔记

## 作用
`repair-desktop` 用来修复 Codex Desktop 中“本地会话文件还在，但左侧线程列表看不到”的问题。

它主要解决这几类状态不一致：

- `~/.codex/session_index.jsonl` 缺失或内容过旧
- Desktop 状态文件里的 workspace roots 不完整
- `~/.codex/state_*.sqlite` 里的 `threads` 表没有同步到现有 session
- 部分 Desktop session 的 provider 标记与当前环境不一致

---

## 入口命令

源码模式下常用命令：

```bash
cd /Users/attention/Documents/claude_daily/codex-session-cloner
PYTHONPATH=src python3 -m codex_session_toolkit repair-desktop
```

预演模式：

```bash
PYTHONPATH=src python3 -m codex_session_toolkit repair-desktop --dry-run
```

核心实现位置：

- `src/codex_session_toolkit/services/repair.py`

---

## 整体流程

### 1. 定位 Codex 数据目录和状态文件
工具通过 `CodexPaths` 定位这些关键文件：

- `~/.codex/sessions/`
- `~/.codex/archived_sessions/`
- `~/.codex/session_index.jsonl`
- `~/.codex/.codex-global-state.json` 或对应状态文件
- `~/.codex/state_*.sqlite`

它还会自动选出最新的 `state_*.sqlite` 作为 Desktop 线程数据库。

---

### 2. 扫描所有 session 文件
工具遍历 session JSONL 文件，逐个解析，提取这些信息：

- `session_meta.payload.id`
- `source`
- `originator`
- `cwd`
- `timestamp`
- `cli_version`
- 第一条用户消息
- `turn_context` 中的：
  - `sandbox_policy`
  - `approval_policy`
  - `model`
  - `effort`

这些信息会被整理成内存中的 `entries` 列表，后面统一用于重建索引、更新状态、写 SQLite。

---

### 3. 判断 session 类型
工具会根据 `source` 和 `originator` 判断 session 是：

- `desktop`
- `cli`
- `unknown`

判断逻辑在：

- `src/codex_session_toolkit/support.py`
- `classify_session_kind(...)`

例如：

- `source == "vscode"` → Desktop
- `source == "cli"` → CLI
- `originator` 含 `Desktop` → Desktop

如果执行时带 `--include-cli`，还会把 CLI session 也按 Desktop 方式处理。

---

### 4. 必要时修正 session_meta
如果某个 Desktop-like session 的 `model_provider` 与当前目标 provider 不一致，工具会修改该 session 文件中的 `session_meta.payload.model_provider`。

如果带了 `--include-cli`，它还会把 CLI session 改写成更像 Desktop 的元数据，例如：

- `source = "vscode"`
- `originator = "Codex Desktop"`
- 对齐 `model_provider`

这一步会直接改写 session 文件，但改写前会先备份。

---

### 5. 重建 `session_index.jsonl`
工具会根据扫描结果重新生成索引文件：

- `~/.codex/session_index.jsonl`

索引里每条记录主要包含：

- `id`
- `thread_name`
- `updated_at`

这一步的作用是让 Desktop 能重新认识这些会话。

---

### 6. 修复 workspace roots
工具会从各个 session 的 `cwd` 提取工作目录，并把这些目录补到状态文件中：

- `electron-saved-workspace-roots`
- `active-workspace-roots`
- `project-order`

这一步的目的：

- 避免会话虽然存在，但因为对应 workspace root 没登记，Desktop 不显示

工具会尽量取 `cwd` 的最近存在父目录，避免写入已经不存在的路径。

---

### 7. 同步 SQLite `threads` 表
这是最关键的一步。

工具会往 `~/.codex/state_*.sqlite` 的 `threads` 表执行 upsert：

- 如果线程不存在 → `insert`
- 如果线程已存在 → `update`

写入的字段包括：

- `id`
- `rollout_path`
- `created_at`
- `updated_at`
- `source`
- `model_provider`
- `cwd`
- `title`
- `sandbox_policy`
- `approval_mode`
- `cli_version`
- `first_user_message`
- `memory_mode`
- `model`
- `reasoning_effort`
- `archived`
- `archived_at`

Desktop 左侧线程显示很依赖这张表，所以它是“恢复可见性”的核心。

---

## Dry-run 与正式执行的区别

### `--dry-run`
只计算和展示：

- 会扫描到多少有效 session
- 会更新多少 threads
- 会补多少 workspace roots

但不会真正写入任何文件或数据库。

### 正式执行
会真实修改：

- session 文件（如果需要 retag / convert）
- `session_index.jsonl`
- 全局状态文件
- `state_*.sqlite`

---

## 备份策略
正式执行时，工具会自动备份目标文件到：

```text
~/.codex/repair_backups/visibility-时间戳/
```

可能备份的内容包括：

- 被改写的 session 文件
- `session_index.jsonl`
- 状态文件
- `state_*.sqlite`

所以它属于“有备份的修复”，不是直接无保护覆盖。

---

## 本次排查到的真实问题
在本机数据中，发现有一条 session 的：

- `session_meta.payload.source`

不是字符串，而是一个结构化对象：

```python
{'subagent': {'thread_spawn': {...}}}
```

这类 session 很像是多 Agent / 子线程派生出来的会话。

原始 `repair-desktop` 假设 `source` 一定是字符串，因此在写 SQLite `threads.source` 时，把 `dict` 直接作为绑定参数传给 SQLite，导致报错：

```text
sqlite3.InterfaceError / ProgrammingError
Error binding parameter 5: type 'dict' is not supported
```

修复方式是：

- 对 `source` / `originator` 做字符串归一化
- 对写入 SQLite 的值做安全转换，只允许传入 SQLite 支持的类型

---

## 结论
`repair-desktop` 的本质不是恢复对话正文，而是：

1. 扫描本地现存 session
2. 重新建立索引
3. 补足 workspace roots
4. 把线程元数据同步回 Desktop 的 `threads` 数据库

因此它修的是“可见性”和“索引一致性”，而不是“从外部找回丢失数据”。

如果 session 文件本身还在，`repair-desktop` 通常就有机会把它们重新恢复到 Codex Desktop 左侧列表中。
