# 安装指引（分步照做）

本文所有 `<...>` 都是占位符，请替换成你自己的值：

| 占位符 | 含义 | 示例 |
| --- | --- | --- |
| `<REPO_DIR>` | 本仓库放置位置 | `~/code/dsh-astrbot-memory-bridge` |
| `<ASTROBOT_HOME>` | AstrBot 安装根目录（含 `data/`） | `~/astrbot` |
| `<DSH_HOME>` | DSH 数据目录 | `~/.dsh` |
| `<LOCAL_PORT>` | 导出端点端口，默认 6197 | `6197` |
| `<LOCAL_TOKEN>` | 导出端点令牌（自己定，别用默认值） | `换成你自己的随机串` |
| `<STATE_DB>` | 同步库路径 | `~/.dsh-bridge/sync-state.db` |

前置条件：

- Python ≥ 3.10（只用标准库，无需 pip 安装）
- Node.js ≥ 22.13（DSH 插件用内置 `node:sqlite`）
- AstrBot ≥ 4.27.0，且陪伴插件「我会永远陪着你」已经完成账号/角色绑定并产生了数据库
  （没绑定前不会有 `companion-v2.sqlite3`，这是正常现象）

---

## 1. AstrBot 侧：装导出插件（只读 HTTP）

1. 复制插件目录：

   ```bash
   cp -r <REPO_DIR>/astrbot-plugin/dsh_bridge_exporter <ASTROBOT_HOME>/data/plugins/
   ```

2. 在 AstrBot 的插件配置里填写（或直接改 `<ASTROBOT_HOME>/data/config/*.json` 对应项）：

   | 键 | 填什么 |
   | --- | --- |
   | `db_root` | `<ASTROBOT_HOME>/data/plugin_data/astrbot_plugin_private_companion` |
   | `exporter_path` | `<REPO_DIR>/bridge/export_companion.py`（也可填它所在目录） |
   | `export_token` | `<LOCAL_TOKEN>` |
   | `listen_port` | `<LOCAL_PORT>` |

   也可以用环境变量：`DSH_BRIDGE_DB_ROOT` / `DSH_BRIDGE_EXPORTER` / `DSH_BRIDGE_TOKEN` / `DSH_BRIDGE_PORT`。

3. 重启 AstrBot，然后验证：

   ```bash
   curl "http://127.0.0.1:<LOCAL_PORT>/health?token=<LOCAL_TOKEN>"
   ```

   期望：`{"ok": true, "db": "<ASTROBOT_HOME>/…/companion-v2.sqlite3", "port": <LOCAL_PORT>}`

   - 返回 `unauthorized`：令牌不一致。
   - 返回 `db: null`：`db_root` 没配对，或陪伴插件还没建库。
   - 连接被拒：插件没加载成功，看 AstrBot 日志里的 `[dsh_bridge_exporter]` 行。

---

## 2. 桥接：导出 + 同步

### 方式 A：HTTP（推荐，只要一条命令）

```bash
python <REPO_DIR>/bridge/sync.py \
  --url "http://127.0.0.1:<LOCAL_PORT>/export" \
  --token "<LOCAL_TOKEN>" \
  --state "<STATE_DB>"
```

### 方式 B：文件（不开端口）

```bash
# 1) 只读导出成 JSONL
python <REPO_DIR>/bridge/export_companion.py \
  --root "<ASTROBOT_HOME>/data/plugin_data/astrbot_plugin_private_companion" \
  --out ./export/companion-export.jsonl

# 2) 同步进状态库
python <REPO_DIR>/bridge/sync.py --input ./export/companion-export.jsonl --state "<STATE_DB>"
```

成功输出形如：

```json
{"input": 128, "inserted": 128, "updated": 0, "deleted": 0, "skipped": 0,
 "errors": 0, "active": 128, "tombstones": 0, "state": "<STATE_DB>"}
```

- 第二次跑同样数据：`inserted=0 updated=0 skipped=128`（幂等）
- 源端改了一条：`updated=1`
- 源端删了一条：`deleted=1`（tombstone，之后不再注入）

想先看统计不落库：加 `--dry-run`。

### 定时（可选，v0.1.0 未内置）

Windows 计划任务示例（每 10 分钟）：

```powershell
$action  = New-ScheduledTaskAction -Execute "python" `
  -Argument "<REPO_DIR>\bridge\sync.py --url http://127.0.0.1:<LOCAL_PORT>/export --token <LOCAL_TOKEN>"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "dsh-astrbot-sync" -Action $action -Trigger $trigger
```

Linux/macOS 用 cron 或 systemd timer 同理。

---

## 3. DSH 侧：装插件

1. 复制插件目录到 DSH 的插件目录：

   ```bash
   cp -r <REPO_DIR>/dsh-plugin <DSH_HOME>/plugins/dsh-astrbot-memory-bridge
   ```

2. 在 DSH profile（`<DSH_HOME>/profiles/<profile>/package.json`）里把它作为本地依赖注册，
   **改动前先备份**：

   ```powershell
   Copy-Item "<DSH_HOME>\profiles\<profile>\package.json" `
             "<DSH_HOME>\profiles\<profile>\package.json.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
   ```

   在 `dependencies` 里加：

   ```json
   "dsh-astrbot-memory-bridge": "link:<DSH_HOME>/plugins/dsh-astrbot-memory-bridge"
   ```

   插件的 `package.json` 自带 `dsh.bundle.patch`（`cordis.patch.yml`），bundle 机制会把它插进条目列表；
   如果你的 DSH 版本需要手动列出 bundles，把 `dsh-astrbot-memory-bridge` 加进 bundles 数组。

3. 让 profile 能解析到这个依赖（二选一）：

   - 跑一次 profile 的依赖安装（`npm install` / `pnpm install`）；
   - 或直接建链接：`mklink /J "<profile>\node_modules\dsh-astrbot-memory-bridge" "<DSH_HOME>\plugins\dsh-astrbot-memory-bridge"`；
     PowerShell：`New-Item -ItemType Junction -Path … -Target …`

4. 重启 DSH，在插件页确认状态为「运行中」，并检查工具列表里有：

   - `astrbot_memory_search`
   - `astrbot_memory_recent`
   - `astrbot_sync_status`

5. 想让注入库路径指向自定义位置，给插件配置 `dbPath`，或设环境变量 `DSH_ASTRBOT_SYNC_DB`
   （默认 `~/.dsh-bridge/sync-state.db`，与 `sync.py` 默认值一致）。

---

## 4. 验收清单

- [ ] `GET /health` 返回 `ok: true` 且 `db` 非 null
- [ ] `sync.py` 输出 `active > 0`
- [ ] DSH 插件页显示运行中
- [ ] 三个工具都出现在工具列表
- [ ] 新开一轮对话，system prompt 里出现「【来自 QQ 陪伴的近期记忆】」段落
- [ ] 问「你还记得我们之前聊过…」，模型能调用 `astrbot_memory_search` 并命中

排查顺序：**先看库有没有数据**（`sqlite3 <STATE_DB> "select count(*) from memories"`），
**再看注入**（`astrbot_sync_status` 工具的输出），**最后看模型**（工具名是否对得上）。

---

## 5. 升级 / 卸载

**升级**：覆盖 `dsh-plugin/` 与 `astrbot-plugin/dsh_bridge_exporter/`，重启两侧；
同步库结构是 `CREATE TABLE IF NOT EXISTS`，向后兼容，不需要迁移。

**卸载**：

1. 从 DSH profile 的 `dependencies` 移除 `dsh-astrbot-memory-bridge`，删掉 `<DSH_HOME>/plugins/dsh-astrbot-memory-bridge`，重启 DSH。
2. 删掉 `<ASTROBOT_HOME>/data/plugins/dsh_bridge_exporter`，重启 AstrBot。
3. `<STATE_DB>` 可留可删（它含真实记忆，删之前确认不再需要）。

---

## 6. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `/health` 一直 `db: null` | 陪伴插件还没建库（未完成绑定），或 `db_root` 指错了层级；应指向 `…/plugin_data/astrbot_plugin_private_companion` |
| `sync.py` 报 `fetch failed` | 端口没监听 / 令牌不对 / AstrBot 没重启 |
| `active=0` 但 `input>0` | 记录都被判为「未见过就删除」，检查导出器的 `state` 字段判定（`DELETED_STATES`） |
| DSH 里完全没有注入 | 插件没真正挂载：看日志有没有 `prompt context 已挂载`；再看 `systemPrompt` 是否提供 `variable/context` |
| 注入有了但检索不到 | 检索走 `LIKE`，注意关键词是否含空格（空格分隔 = OR） |
| 端口已被占用 | 导出插件会打印 warning 并沿用已在监听的实例；确认是不是重复启动了两个 AstrBot |
| Windows 上 `python` 打不开 | 用 `py -3`，或装正式版 Python（Microsoft Store 的 shim 常常只弹商店页） |