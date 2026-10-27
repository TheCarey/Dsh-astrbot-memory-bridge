# 隐私审计（发布前自检）

发布前的目标只有一句：**仓库里除了作者署名，不出现任何本机私货。**

## 1. 分类与替换策略

| 类别 | 例子（本次实际存在过的） | 处理方式 |
| --- | --- | --- |
| 本机用户目录 | Windows 用户目录含个人姓名 | 路径一律改 `~` / `<USER_HOME>` / `<DSH_HOME>` / `%USERPROFILE%` |
| 绝对路径（Windows 盘符） | 盘符根下的私人项目目录 | 改成 CLI 参数 / 环境变量 / 占位符 |
| AstrBot 实例 GUID | 形如 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` 的目录名 | 删除，改为 `<ASTROBOT_HOME>` |
| QQ 号 | 9–11 位纯数字 | 合成值 `10000` / `10001` / `20000` |
| 机器人昵称 / 角色名 | 自定义昵称 | 改成 `companion` / `助手` / `用户` |
| 令牌 / 密码 | 任何 16 位以上十六进制串、`token=` 后面的值 | 换成 `<LOCAL_TOKEN>`，默认值只留 `local-dev-token` |
| 真实记忆正文 | 聊天记忆、日记 | 整份删除；只保留纯合成示例 |
| 运行时产物 | `*.db`、`*.db.bak-*`、`*.log`、`__pycache__`、`node_modules` | 不入库（`.gitignore` + 手工排除） |

## 2. 扫描命令

在**成品目录**里执行（示例为 bash / Git Bash；PowerShell 用 `Select-String` 等价替代表达式）。

```bash
# 2.1 绝对路径：Windows 用户目录、盘符路径
grep -rInE '[A-Za-z]:[\\/]Users[\\/]|[A-Za-z]:\\(Companion|DSH|Projects|Work)' .

# 2.2 实例 GUID（8-4-4-4-12 十六进制）
grep -rInE '\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b' .

# 2.3 长数字（QQ 号、手机号）：9–11 位独立数字
grep -rInE '(^|[^0-9])[0-9]{9,11}([^0-9]|$)' . --include='*.py' --include='*.js' --include='*.md' --include='*.json' --include='*.yml'

# 2.4 凭据形态
grep -rInE '(sk-[A-Za-z0-9]{10,}|Bearer [A-Za-z0-9._-]{10,}|token\s*[=:]\s*["'\''][^"'\''<]{12,}|password\s*[=:])' .

# 2.5 个人昵称 / 角色名：先把你知道的昵称填进尖括号，再扫
grep -rInE '(<你的昵称1>|<你的昵称2>|<你的角色名>)' .

# 2.6 运行时产物是否混入
git ls-files | grep -Ei '\.(db|sqlite3|log|zip)$|state\.db|__pycache__|node_modules'
```

## 3. 本次结果

| 检查 | 结果 |
| --- | --- |
| 2.1 绝对路径 | 0 命中（文档里只有 `<ASTROBOT_HOME>`、`<REPO_DIR>`、`~/.dsh-bridge/…` 这类占位符） |
| 2.2 实例 GUID | 0 命中 |
| 2.3 长数字 | 0 命中（示例数据统一用 `10000/10001/20000`，时间戳为 ISO 格式） |
| 2.4 凭据形态 | 0 命中（默认令牌为公开的开发用值 `local-dev-token`，README 已提示替换） |
| 2.5 昵称 | 0 命中（统一 `companion` / `助手` / `用户`） |
| 2.6 产物 | 0 命中（`*.db`、`export/`、`*.log`、`__pycache__/` 均被 `.gitignore` 排除） |
| 例外（允许出现） | `TheCarey`（MIT 署名）、项目名 `dsh-astrbot-memory-bridge`、上游项目名（兼容性说明） |

> 扫描是「模式化」的：它证明**这些形态的串**不存在，不等于证明「绝对没有任何隐私」。
> 提交前仍建议人工过一遍 `git diff --cached`，尤其是新增的示例数据。

## 4. 提交前的固定动作

1. `git status --porcelain` 里**不应**出现 `*.db` / `export/` / `*.log`。
2. `git diff --cached | grep -inE '(token|secret|password|cookie|authorization)'` 逐条确认是占位符。
3. 示例数据（`bridge/fixtures/*.jsonl`）通读一遍，确认是「公式化的假句子」。
4. 如果你曾经把真实 `sync-state.db` 复制进过仓库目录，先删掉再 `git add`；
   已经 commit 过的要用 `git rm --cached` + 重写历史（或干脆重开仓库）。

## 5. 使用者自查（部署后）

```bash
# 你自己的同步库不该进任何 git 仓库
git check-ignore -v ~/.dsh-bridge/sync-state.db

# 导出文件同样是隐私数据，删之前想清楚
ls -l ./export/
```