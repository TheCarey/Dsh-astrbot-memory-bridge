# dsh_bridge_exporter（AstrBot 侧插件）

只读导出陪伴插件「我会永远陪着你」（`astrbot_plugin_private_companion`）的记忆，
给 DSH 侧的 `dsh-astrbot-memory-bridge` 拉取。

## 端点（只绑 127.0.0.1）

| 端点 | 说明 |
| --- | --- |
| `GET /health?token=<TOKEN>` | 存活 + 数据库定位结果 `{"ok":true,"db":"…","port":6197}` |
| `GET /export?since=<ISO>&token=<TOKEN>` | 增量导出（`since` 可省）→ JSONL，每行一条记录 |

鉴权：`?token=` 查询参数或 `X-Bridge-Token` 请求头，二者任一命中即可。
未授权返回 `401 {"ok":false,"error":"unauthorized"}`。

## 配置

| 键 | 环境变量 | 默认 |
| --- | --- | --- |
| `db_root` | `DSH_BRIDGE_DB_ROOT` | 空（必须配） |
| `exporter_path` | `DSH_BRIDGE_EXPORTER` | 自动查找 |
| `export_token` | `DSH_BRIDGE_TOKEN` | `local-dev-token` |
| `listen_port` | `DSH_BRIDGE_PORT` | `6197` |

`db_root` 指向 `<ASTROBOT_HOME>/data/plugin_data/astrbot_plugin_private_companion`；
插件会在其下递归查找 `companion-v2.sqlite3`。

`exporter_path` 指向仓库里的 `bridge/export_companion.py`（文件或所在目录）。
留空时的查找顺序：插件目录 → 从插件目录向上最多 5 层的 `bridge/`、`scripts/`、根目录。

## 行为

- 数据库始终以 `file:…?mode=ro` 打开；本插件**不写**任何数据。
- 端口被占用（10048/48/98）时打 warning 并沿用已在监听的实例，不重复绑定、不崩溃。
- `db_root` 未配置时插件照常加载，`/health` 返回 `db: null`，`/export` 返回 404 并说明原因。

## 安装

见仓库根目录 [INSTALL.md](../../INSTALL.md) 第 1 节。