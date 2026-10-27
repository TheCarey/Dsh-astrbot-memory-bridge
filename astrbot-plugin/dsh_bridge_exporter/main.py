# -*- coding: utf-8 -*-
"""dsh_bridge_exporter —— AstrBot 侧导出端点（只读）。

把陪伴插件「我会永远陪着你」（astrbot_plugin_private_companion）的
companion-v2.sqlite3 以只读方式导出成稳定的 JSONL，供 DSH 侧的
dsh-astrbot-memory-bridge 同步器拉取。

* 只读打开数据库（file:...?mode=ro），绝不写陪伴插件的数据。
* 默认只监听 http://127.0.0.1:<LOCAL_PORT>，需带 ?token=<token> 或 X-Bridge-Token 头。
* 端点：
    GET /health              存活与数据库定位结果
    GET /export?since=<iso>  增量导出（since 为空则全量），返回 JSONL（每行一条记录）

配置（AstrBot 插件配置，或环境变量）：
    export_token   访问令牌（环境变量 DSH_BRIDGE_TOKEN，默认 local-dev-token，仅本机可用）
    listen_port    端口（环境变量 DSH_BRIDGE_PORT，默认 6197）
    db_root        陪伴插件数据根（环境变量 DSH_BRIDGE_DB_ROOT），指向
                   <ASTROBOT_HOME>/data/plugin_data/astrbot_plugin_private_companion
    exporter_path  桥接脚本 export_companion.py 的路径或所在目录
                   （环境变量 DSH_BRIDGE_EXPORTER；留空时自动向上查找仓库相对位置）
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import Context, Star, register

try:  # aiohttp 是 AstrBot 自带依赖
    from aiohttp import web
except Exception:  # pragma: no cover
    web = None  # type: ignore

DEFAULT_TOKEN = "local-dev-token"
DEFAULT_PORT = 6197
DEFAULT_DB_ROOT = ""
EXPORTER_ENV = "DSH_BRIDGE_EXPORTER"
# 仓库内的相对位置：astrbot-plugin/dsh_bridge_exporter/ → ../../bridge/export_companion.py
REPO_RELATIVE = (
    Path("bridge") / "export_companion.py",
    Path("scripts") / "export_companion.py",
    Path("export_companion.py"),
)


def _candidates(configured: str, plugin_dir: Path) -> list[Path]:
    """按优先级列出 export_companion.py 的候选位置。"""
    out: list[Path] = []

    def add(value: str | None) -> None:
        if not value:
            return
        p = Path(value).expanduser()
        out.append(p / "export_companion.py" if p.is_dir() else p)

    add(configured)
    add(os.environ.get(EXPORTER_ENV))
    add(str(plugin_dir / "export_companion.py"))
    # 从插件目录往上最多 5 层，找仓库里的 bridge/export_companion.py
    for parent in list(plugin_dir.parents)[:5]:
        for rel in REPO_RELATIVE:
            out.append(parent / rel)
    return out


def _load_exporter(configured: str, plugin_dir: Path):
    """按路径加载 export_companion.py（不改 sys.path 全局状态）。"""
    tried = _candidates(configured, plugin_dir)
    path = next((p for p in tried if p.is_file()), None)
    if path is None:
        listing = "、".join(str(p) for p in tried) or "(无)"
        raise FileNotFoundError(
            "找不到 export_companion.py。请在 AstrBot 插件配置里设置 exporter_path，"
            f"或设置环境变量 {EXPORTER_ENV}。已尝试：{listing}"
        )
    spec = importlib.util.spec_from_file_location("dsh_bridge_export_companion", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"无法加载导出模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@register(
    "dsh_bridge_exporter",
    "TheCarey",
    "把陪伴插件记忆只读导出给 DSH 桥接（默认 127.0.0.1:6197）",
    "0.1.0",
)
class DshBridgeExporter(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        cfg = config or {}
        self.token = str(cfg.get("export_token") or os.environ.get("DSH_BRIDGE_TOKEN") or DEFAULT_TOKEN)
        self.port = int(cfg.get("listen_port") or os.environ.get("DSH_BRIDGE_PORT") or DEFAULT_PORT)
        self.db_root = str(cfg.get("db_root") or os.environ.get("DSH_BRIDGE_DB_ROOT") or DEFAULT_DB_ROOT)
        self.exporter_path = str(cfg.get("exporter_path") or "")
        self._runner = None
        self._site = None
        self._exporter = None

    async def initialize(self):
        if web is None:
            logger.error("[dsh_bridge_exporter] aiohttp 不可用，导出端点未启动")
            return
        plugin_dir = Path(__file__).resolve().parent
        try:
            self._exporter = _load_exporter(self.exporter_path, plugin_dir)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[dsh_bridge_exporter] 加载导出模块失败：{exc}")
            return
        if not self.db_root:
            logger.warning(
                "[dsh_bridge_exporter] 未配置 db_root，/health 与 /export 会报库未找到；"
                "请指向 <ASTROBOT_HOME>/data/plugin_data/astrbot_plugin_private_companion"
            )
        if self.token == DEFAULT_TOKEN:
            logger.warning(
                "[dsh_bridge_exporter] 正在使用默认令牌 local-dev-token（仅本机 127.0.0.1 可访问）。"
                "如需更强隔离，请设置 export_token。"
            )

        token = self.token
        exporter = self._exporter
        db_root = self.db_root

        def authorized(request: web.Request) -> bool:
            return request.query.get("token") == token or request.headers.get("X-Bridge-Token") == token

        async def health(request: web.Request):
            if not authorized(request):
                return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
            db = exporter.find_db(db_root) if db_root else None
            return web.json_response({"ok": True, "db": db, "port": self.port})

        async def export(request: web.Request):
            if not authorized(request):
                return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
            if not db_root:
                return web.json_response({"ok": False, "error": "db_root not configured"}, status=404)
            since = request.query.get("since")
            db = exporter.find_db(db_root)
            if not db:
                return web.json_response({"ok": False, "error": "companion-v2.sqlite3 not found"}, status=404)
            try:
                rows = await asyncio.to_thread(exporter.export, db, since)
            except Exception as exc:  # noqa: BLE001
                return web.json_response({"ok": False, "error": str(exc)}, status=500)
            body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
            return web.Response(text=body, content_type="application/x-ndjson")

        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_get("/export", export)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        try:
            self._site = web.TCPSite(self._runner, "127.0.0.1", self.port, reuse_address=True)
            await self._site.start()
            logger.info(
                f"[dsh_bridge_exporter] 已监听 http://127.0.0.1:{self.port} (db_root={self.db_root or '<未配置>'})"
            )
        except OSError as exc:
            winerr = getattr(exc, "winerror", None)
            errno_ = getattr(exc, "errno", None)
            if winerr == 10048 or errno_ in (48, 98, 10048):
                logger.warning(
                    f"[dsh_bridge_exporter] 端口 {self.port} 已被占用（可能已有实例在跑），"
                    "本插件继续加载，导出端点沿用已在监听的那个实例"
                )
                self._site = None
                try:
                    await self._runner.cleanup()
                except Exception:
                    pass
                self._runner = None
            else:
                raise

    async def terminate(self):
        try:
            if self._runner is not None:
                await self._runner.cleanup()
                logger.info("[dsh_bridge_exporter] 导出端点已关闭")
        except Exception:  # noqa: BLE001
            pass