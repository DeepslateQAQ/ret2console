# ret2console — 设计规格说明书

> 本文档是 ret2console 的唯一设计权威。任何实现偏差都以本文档为准；发现文档与事实冲突时，先改文档再改代码。

## 1. 项目定位

ret2console（简称 r2c）是 ret2shell（回归终端，`github:ret2shell/ret2shell`）比赛平台的**终端用户接口**（CLI 客户端）。类比：Codex CLI 之于 ChatGPT。ret2console 本身不实现权限管理、不保存业务数据，一切业务逻辑与权限均以 ret2shell 后端为准。

- 后端：https://github.com/ret2shell/ret2shell（Rust + Axum，Postgres + Valkey + NATS + K8s）
- API 文档：https://www.woooo.tech/zh-cn/ret2shell/references/
- 默认 API 地址：`https://ctf.xidian.edu.cn/api`（西电 CTF 终端，可在 config 中修改）
- 界面语言：全中文

## 2. 技术栈

- Python ≥ 3.11，单包 `ret2console`，uv + pyproject.toml，hatchling 构建
- 双入口：`ret2console` 与 `r2c`（指向同一 main）
- 依赖：prompt-toolkit（REPL/补全/高亮）、rich（表格/输出渲染）、httpx（异步 HTTP）
- 全部 async：REPL 用 `PromptSession.prompt_async`，API 用 `httpx.AsyncClient`
- 测试：pytest；API 层用 httpx MockTransport 离线打桩；REPL 逻辑抽成纯函数测试

## 3. 运行模式

### 3.1 REPL（默认）

无参数启动即进入常驻 shell，提示符见 §5。

### 3.2 `-c "cmd; cmd; ..."`

一次性执行，支持分号分隔多条命令，执行后退出；失败退出码透传（非零）；非 TTY 自动去色。

### 3.3 shebang / 脚本文件

`#!/usr/bin/env ret2console` 或 `ret2console <script>`：逐行执行，`#` 开头为注释，空行跳过；失败即停（类似 `bash -e`），退出码透传；非 TTY 输出去色。

脚本模式认证策略：需要认证的命令若无已保存会话 → 报错退出（不交互）；有则直接使用已保存的认证信息。交互式密码输入只属于 REPL。

## 4. 认证与验证码

### 4.1 验证码：PoW（定案）

ret2shell 为 CLI 提供专用验证码通道，登录/注册一律走 PoW，不做终端内图形验证码显示：

- `GET /api/account/captcha/cli` → 永远返回 `ValidatorType::Pow`（不依赖平台配置）
- 响应：`{id, validator: "pow", challenge: "<difficulty>#<seed16位>"}`（criteria 可能被脱敏，从 challenge 解析即可）
- 求解：答案 `ans` 必须 `ans.startswith(seed)` 且 `sha256(ans)` 的十六进制以 `difficulty` 个 `0` 开头；暴力递增后缀，难度 4 平均 65536 次，毫秒级
- 提交：`{account, password, captcha_id, captcha_answer}`
- 注意：同一账号连续失败 5 次冻结 30 分钟

### 4.2 Token 机制（关键坑）

- 登录成功响应体为空，token 通过响应头 `Set-Token` 下发
- 之后所有请求带 `Authorization: Bearer <jwt>`；token 有效性依赖服务端缓存（非纯无状态 JWT）
- **token 临近过期时服务端会自动续期，新 token 同样通过任何响应的 `Set-Token` 头返回——客户端必须监听每个响应头并更新本地 token**
- 登出使服务端 token 失效

### 4.3 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/account/captcha/cli` | GET | PoW 验证码 |
| `/api/account/login` | POST | `{account, password, captcha_id, captcha_answer}` |
| `/api/account/register` | POST | `{account, nickname, email, password, captcha_id, captcha_answer}` |
| `/api/account/logout` | POST | 登出 |
| `/api/account/profile` | GET | 当前用户信息 |

## 5. 提示符

```
未登录  绿色  [guest@ret2console:/path]$
已登录  绿色  [user@ret2console:/path]$
管理员  红色  [admin@ret2console:/path]#   （管理员以 # 结尾，类似 root）
```

`/path` 为虚拟文件系统当前路径（§7），完整显示；超过终端宽度截断左端保留尾部。登录后显示 nickname；管理员判定依据登录时返回的权限集。

## 6. 语法高亮（zsh-syntax-highlighting 风格）

- 合法命令（命令集内）→ 绿色 bold + 下划线（对齐 zsh-syntax-highlighting 的 suffix-alias/precommand 样式）；未知命令 → 红色 bold
- 路径（以 `/` 开头或命中 fs 节点）→ 蓝色下划线
- 普通参数 → 默认色；注释（`#` 开头）→ 亮灰（`fg=black,bold`）；引号内容 → 黄色；glob（`* ? []`）→ 蓝色
- flag 整行匹配（§8）→ 整行金色，输入区下方实时提示「将以 flag 提交」

> 配色对齐 zsh-syntax-highlighting 默认主题（ANSI 8 色 + bold 亮化），不使用偏暗的 256 色。

## 6.1 交互提示（GitHub CLI 风格）

- 输入提示：`? 标题: `（青色问号 + 粗体标题）；密码隐藏回显
- 确认：`? 标题 [y/N] `
- 选择器：↑↓ 高亮 + Enter + 数字快捷（autoplay 的 Agent 选择）
- 成功/失败/提示前缀：`✓` 绿 / `✗` 红 / `!` 黄

## 7. 虚拟文件系统（pseudo filesystem）

终端内模拟只读虚拟文件系统，`cd` 即 `use` 语义：

```
/
├── notice            # 全局公告（bulletin）
├── contests/         # ls = 比赛列表（动态，来自 API）
│   └── <game_id>/
│       ├── info      # cat = 比赛详情
│       ├── notice    # cat = 比赛通知
│       ├── scoreboard# cat = 排行榜（GET /api/game/{id}/team?min_state=passed&order=score&asc=false）
│       ├── docs/     # readme / training / rules（GET /api/game/{id}/doc/{doc}）
│       ├── team      # cat = 当前队伍（GET /api/game/{id}/team/self）
│       └── challenges/  # ls = 题目列表（动态）
│           └── <challenge_id>/
│               ├── README   # cat = 题面（markdown 渲染）
│               ├── hint     # cat = 提示
│               ├── files/   # ls = 附件列表；zip 附件 cat 给出提示信息（不直接输出）
│               ├── instance # cat = 靶机实例状态
│               └── flag     # 提交入口（见 §8）
```

- 节点用 **id**（game_id / challenge_id）命名，`ls` 显示 id → 标题映射
- 进入比赛目录 = 设定当前比赛上下文；进入题目目录 = 设定当前题目上下文
- 命令与 fs 共享同一套上下文：`cd /contests/3/challenges/5` 后 `submit`、`hint`、`instance` 默认作用于题目 5；命令式 `contest use 3` / `challenge show 5` 等价于 cd，互为镜像
- 附件下载：`download <题目> <文件>` 下载到真实 cwd（像 wget），文件名冲突自动加 `(1)` 后缀
- 公告：根 `/notice` = 全局公告；比赛下 `notice` = 比赛通知

## 8. Flag 提交

- 显式：`submit <flag>`；`submit` 无参进入安全粘贴模式
- **粘贴即提交**：整行匹配 `^[A-Za-z0-9_]+\{[^{}\n]+\}$`（先 trim 首尾空白/换行）自动走提交流程，无确认弹窗
- 命中 flag 的行**不进命令历史**（防泄露）
- 无登录/无当前题目上下文时给出明确提示，不静默失败
- 提交结果即时反馈（正确/错误/其他后端语义）
- 限流：后端 10 次/窗口
- 提交 API：`POST /api/game/{gid}/challenge/{cid}/submission`，body `{"content": "<flag>"}`

## 9. 命令集

| 分组 | 命令 |
|---|---|
| 认证 | `auth login` `auth logout` `auth register` `auth status` `auth endpoint [URL] [--insecure]`（切换 API 地址，清除旧登录态；`reset` 恢复默认） |
| 比赛 | `contest list` `contest show <id>` `contest use <id>`（= cd）` contest doc <id> <type>` |
| 队伍 | `team create <name>` `team join <token>` `team modify <name> [tag]` `team info` `team rank` `team member` `team solve` `team leave` |
| 题目 | `challenge list` `challenge show <id>`（cat）` challenge hint <id>` `challenge unlock <id> <hint_id>` `challenge start <id>` `challenge instance <id>` `challenge renew <id>` |
| 附件 | `download <题目> <文件>` |
| 提交 | `submit [flag]` |
| 排行 | `scoreboard` |
| 管理 | `admin bulletin <list|show|create|update|delete>` `admin user <list|show|update|delete|ip>` `admin game <list|show|create|update|delete|token|admins|submissions|audit|stats>` `admin challenge <list|show|create|update|delete|publish|unpublish|answer|history|submissions|instances|attach>` `admin platform <stats|logs|config>` `admin cluster <nodes|config|calmdown>` |
| 系统 | `help` `exit` `clear` `version` `echo` `pwd` `ls` `cd` `status` |

shell 彩蛋别名：`ls` = `challenge list`、`cat` = `challenge show`（仅限题目上下文内）、`wget` = `download`。公告/通知：`notice`。

### 9.1 输出符号约定

- `[green]✓[/green]` — 操作成功（确定态）
- `[yellow]➜[/yellow]` — 已提交/完成态但不一定成功（如已进入评测队列、实例启动请求已提交）
- `[red]✗[/red]` — 操作失败（API 错误、无效参数、未知子命令）
- `[yellow]![/yellow]` — 提示/拒绝（未登录、缺少上下文、需要确认的警告）
- 用法引导 `用法: ...` 与 bash 语义的 `command not found` 不带符号

## 10. 补全（三层）

1. 静态命令/子命令补全（离线）
2. fs 路径补全（`cd`/`ls`/`cat`/`download` 输入时 Tab 补目录与文件；同步遍历**已加载**节点，未加载目录自动异步加载（限时 3s）；相对路径保留相对形式）
3. 动态数据补全：比赛/题目列表首次拉取后缓存进会话，`challenge show <Tab>` 补题目 id；`ls` 时刷新缓存

补全菜单 zsh 风格：比赛/题目目录与动态 id 右侧同时显示描述（比赛标题/题目标题，来自 API 缓存）。末尾空白后 Tab 视为开始新词（`auth<空格>` 直接补子命令，不替换已输入内容）。

## 11. 子 shell 注入

REPL 内输入 `zsh` / `bash` / `fish`（检测本机已安装）交互式启动该 shell，注入 handler 提取 flag：

- bash：临时 rc 文件 + `bash --rcfile ...`；rc 内定义 `submit()` 函数 + `DEBUG trap`，每条命令执行前检查整行是否匹配 flag 格式，命中自动提交
- zsh：`ZDOTDIR` 指向临时目录，注册 `preexec` 钩子
- fish：`fish_preexec` 事件监听
- 提交通道：`ret2console -c "submit -"`，flag 从 stdin 喂入（**零注入风险**，flag 内含引号/分号/反引号均安全）
- 只对交互式启动注入；`-c "bash script.sh"` 非交互场景直接透传
- 不注册 `r2c` 函数——`r2c` 是可执行文件，天然在 PATH 中可用
- 未登录时子 shell 内提交按 §3.3 策略报错提示
- 检测到 flag 自动提交 + 打印结果（静默，无确认）

## 12. autoplay（一把梭）

`autoplay` 命令流程：
1. 检测可用 Agent（`shutil.which`）：`codex` / `claude` / `opencode` / `omp` / `reasonix` / `kimi`，多个时弹出 gh 风格交互式选择器（↑↓ 高亮 + Enter，prompt_toolkit 实现，支持数字快捷）
2. 确认提示：「警告：使用一把梭功能可能导致您的亲人遭受损失（物理），是否仍要继续？[y/N]」（文案原样保留）
3. 自动下载当前题目附件到 `~/.ret2console/autoplay/<题目id>/`，生成「任务书」markdown（题面全文 + 附件绝对路径 + 目标说明）
4. 用参数把提示词传进去**直接启动 Agent 的标准 TUI**（继承终端），选手继续操作；不回收 flag，选手退出后自行粘贴提交。
   不支持 TUI 初始消息的 Agent（kimi / reasonix）：先打印提示词，选手自行复制、启动后粘贴到首条消息，确认后启动

Agent 启动方式（实测/待实测）：
- `codex` → `codex "<prompt>"`（无子命令 + PROMPT 参数直接进交互式 TUI 并带初始消息 ✓ 已实测）
- `claude` → `claude "<prompt>"`（交互式，首条消息）
- `opencode` → `opencode --prompt "<prompt>"`（交互式 TUI 带初始消息）
- `omp` → `omp "<prompt>"`（直接进交互式 TUI 并带初始消息，omp 文档确认）
- `kimi` → Kimi Code CLI：`kimi` 进交互式 TUI（`-p` 为单次非交互）；**不支持 TUI 初始消息** → 打印提示词供复制，确认后启动
- `reasonix` → `reasonix code` 进 code 模式 TUI；**不支持 TUI 初始消息** → 打印提示词供复制，确认后启动
- 执行方式以各家 `--help` 实测为准，任务书兜底永远在

## 13. libwsrx 进程内隧道

ret2console 直接依赖 Python 包 `libwsrx`，不检测或下载 `wsrx` CLI，不启动 daemon，
也不注册或调用 wsrx-desktop。`App.wsrx` 是当前应用进程内唯一的隧道所有者。

### 13.1 端点绑定与所有权

- 每条新隧道调用 `await libwsrx.ClientEndpoint.bind("127.0.0.1:0", remote)`；端口由
  操作系统在 bind 时原子分配，命令使用 `endpoint.local_addr` 返回的实际地址。
- 管理器保留 `ClientEndpoint`，并记录 remote、local、game_id、challenge_id。remote URL
  是唯一键；同一进程内重复连接完全相同的 remote 时复用现有端点。
- `pool_instances()` 仅展示当前管理器实际保留的 live 端点；`challenge instance` 和 VFS
  状态都从该列表读取，不使用第二份注册表或磁盘状态。
- `remove`、`remove_for` 和 `close` 先从内存移除记录，再等待 `endpoint.shutdown()`。
  批量清理会尝试关闭所有选中端点；重复 `close()` 是安全的。

### 13.2 进程生命周期

- 端点只在创建它的 ret2console 进程存活期间可用。交互式 REPL 和 autoplay 运行期间，
  管理器持续持有端点。
- REPL、`-c` 和脚本模式退出时都先 `await app.wsrx.close()`，再关闭 API 客户端。
  因此通过 `-c` 或脚本执行 `challenge connect` 后，隧道会随该一次性进程退出而关闭。
- `challenge disconnect`、`challenge stop` 和进程退出均确定性关闭相应 listener 与活跃连接；
  不存在 daemon pid、secret、HTTP `/pool`、desktop lock/control 或 `tunnel.json` 状态文件。

### 13.3 `challenge connect <题目>` 命令

- 选手实例查询走 `GET /api/game/{gid}/instance`（challenge 级 GET 是管理员接口），再按
  `challenge_id` 过滤。
- `instance.traffic` 是平台 nanoid；每个暴露端口的 remote 为
  `wss|ws://{平台域名}/api/traffic/{nanoid}?port={端口}`，与前端 `getWsrxLink` 一致。
- 每个端口建立独立 `ClientEndpoint`，输出其实际 `127.0.0.1:PORT`。若多端口连接中
  后续 bind 失败，本次命令新建的端点全部回滚；此前已存在的端点不受影响。
- `challenge disconnect [id]` 按 game_id/challenge_id 关闭当前进程持有的端点，不停止实例。
- `challenge stop [id]` 先关闭对应端点，再 DELETE
  `/api/game/{gid}/challenge/{cid}/instance`；端点关闭错误不阻止远端实例删除。
- `challenge start` 冲突时提示 `challenge instance` / `challenge stop <题目id>`。
- `challenge start` 的 POST 成功后，最多等待 30 秒，直到实例同时满足 `running` 且
  `traffic` 可用；该就绪期限包含状态查询耗时，并独立于每次 HTTP 请求的传输超时。
  到期时命令返回状态 `0`，提示远端启动仍可能继续，并引导稍后查询状态和连接。
- 在上述提交后等待期间按 Ctrl-C 只停止本地轮询，返回状态 `130`，不撤销服务器端启动，
  交互式客户端随后返回提示符。`challenge connect` 仍是绑定本地隧道的独立命令。

## 14. ret2shell API 参考（已确认）

- 所有路径前缀 `${API_BASE}`（默认 `/api`），无 `/v1`
- 认证中间件：Public / Guest-aware / Basic（登录）/ Verified（登录+邮箱验证）/ 权限分级（Permission::User 等）
- 比赛：`GET /api/game`（列表，分页）、`GET /api/game/{id}`、`GET /api/game/{id}/doc/{doc}`（readme|training|rules）、`GET /api/game/{id}/notification`（比赛通知）
- 排行榜：`GET /api/game/{id}/team?min_state=passed&order=score&asc=false`（分页，返回 `[Team[], count]`）
- 队伍：`GET /api/game/{id}/team/`（列表）、`POST /api/game/{id}/team/`（创建）、`PATCH /api/game/{id}/team/`（加入，带 token）、`PATCH /api/game/{id}/team/self`（修改本队：name 必填，tag 传 null 清空）、`GET /api/game/{id}/team/query?token=`、`GET /api/game/{id}/team/self`、`GET /api/game/{id}/team/{tid}`、`.../rank`、`.../member`、`.../solve`、`.../extra`
- 题目：`GET /api/game/{gid}/challenge`（列表）、`GET /api/game/{gid}/challenge/{cid}`（详情，题面为 markdown 文本）、`GET /api/game/{gid}/challenge/{cid}/hint`、`POST /api/game/{gid}/challenge/{cid}/hint/unlock`、`GET /api/game/{gid}/challenge/{cid}/file`（附件列表/下载，query: folder/file）、`GET /api/game/{gid}/challenge/{cid}/submit`（解题状态 `{solved, solves}`；带 `?id=<提交id>` 返回该次提交详情）
- 提交（**评测队列**，不是 `/submission`，那是管理员接口）：`POST /api/game/{gid}/challenge/{cid}/submit`（body `{"content": ...}`）→ 返回 submission `{id, solved: null, result: null}` 入队；随后 `GET /api/game/{gid}/challenge/{cid}/submit?id=<id>` 轮询，checker 完成后 `solved: bool` + `result: 文本`（失败样例为 "checker fails on your input, incorrect."）；`submit` 命令 POST 后轮询（最多 10 次 × 0.8s），超时提示稍后重查
- 实例（选手）：`GET /api/game/{gid}/instance`（本人实例列表）、`POST/PATCH/DELETE /api/game/{gid}/challenge/{cid}/instance`（启动/续期/关闭）；返回简化 Pod：`state/name/traffic/ports/renew_count/created_at/user_id/user_name/team_id/team_name/challenge_id/challenge_name`
- 公告：`GET /api/bulletin`（分页）
- 事件 WS：`WS /api/game/{id}/event/connect?game_id=&token=` —— **需要 game token（主办方大屏专用），普通用户不可用**，通知一律轮询

### 14.1 管理员接口（admin 命令）

- 权限门禁：r2s 服务端校验（Bulletin/User/Game/Host/DevOps/Calendar/Wiki/Statistics）；本地仅要求已登录且权限集非空
- 公告：`POST /api/bulletin`、`PATCH/DELETE /api/bulletin/{id}`（article model：title 必填，path/publisher_id/created_at 等由命令补齐默认值）
- 用户：`GET /api/user`（filter/page）、`GET/PATCH/DELETE /api/user/{id}`（**email 必填**，命令先 GET 现状再合并）、`GET /api/user/{id}/ip`；`permissions` 传 `game,host` 或 JSON 数组
- 比赛：`POST /api/game`（model 全字段，命令填默认值：host_type=Game、hidden=true、sync=2、时间=now，k=v 覆盖）、`PATCH/DELETE /api/game/{id}`（`--force` 删已建题的比赛）、`POST /api/game/{id}/token`、`GET/PATCH /api/game/{id}/administrator`、`GET /api/game/{id}/submission`、`GET /api/game/{id}/audit` + `PATCH /api/game/{id}/audit/{aid}`（state：1 驳回 / 2 确认，reason 必填）、`GET /api/game/{id}/statistics`
- 题目：`POST /api/game/{gid}/challenge`（创建即 hidden，发布用 publish）、`PATCH/DELETE /api/game/{gid}/challenge/{cid}`、`POST/DELETE .../publish`（上/下架）、`GET/PATCH .../answer`（**body 为裸 JSON 字符串**，如 `"flag{xxx}"`）、`GET .../history|submission|instance`、`POST .../file?folder=static|mapped|checker`（multipart 上传）
- 平台：`GET /api/platform/statistics`、`GET /api/platform/logs`（query/level/limit/from/account/trace）、`GET /api/platform/config`
- 集群：`GET /api/cluster/node`、`GET /api/cluster/config`、`GET /api/cluster/calmdown`
- 时间参数：unix 秒 / ISO8601 / `YYYY-MM-DD HH:MM`（本地时区），自动转 ts_seconds

## 15. 配置与会话存储

- 目录：`~/.ret2console/`
- `nix run .` 直接运行（flake 定义 `packages.default/ret2console` 与 `apps.r2c`；仓库文件需 git tracked）
- `config.json`：API 地址、下载目录等（权限 644）
- `session.json`：JWT、账号、昵称、权限集（权限 **600**）
- `history`：REPL 历史（flag 行过滤后写入）
- JWT 过期自动回到 guest 态，不崩溃；监听 `Set-Token` 头实时更新（§4.2）

## 16. 调试环境

- `/home/deepslate/dev_ret2shell/`：`docker-compose.yml` 一键起全套（postgres/valkey/nats/vlogs/registry:2 + platform 镜像构建自 ret2shell Containerfile）+ .env + config/config.toml（已按 compose 服务名配置）+ README
- 启动：`docker compose up -d --build`（首次构建 Rust release 约 10-20 分钟；`R2S_SRC` 可覆盖源码路径）
- 访问：http://localhost:8080（Web + `/api`）；调试 ret2console 时把 config.json 的 `api_base` 指向 `http://localhost:8080/api`
- 平台数据持久化于 `platform` volume；config 挂载到容器 `/etc/ret2shell/`
- 本机现状：codex ✓、opencode ✓、claude/omp/reasonix/kimi/wsrx 未安装

## 17. 里程碑

1. ✅ 设计定案（本文档）
2. ✅ 骨架：pyproject + 双入口 + REPL + 三态提示符 + 分词高亮 + 静态补全 + config/session 存储
3. ✅ API 客户端 + `auth`（PoW 登录/登出/status；register 参数式 + 交互式）
4. ✅ VFS + `contest`/`challenge` 命令（列表/详情/use/doc/notice；`team` 全套；docs/files/instance 节点已挂接）
5. ✅ 提交：`submit` + flag 粘贴识别 + 历史过滤（含分号内 flag 识别）
6. ✅ 附件下载 + 实例 `connect` + 进程内 libwsrx 端点（`start`/`instance`/`renew`/`connect` 可用）
7. ✅ `bulletin`（根 `/notice`）+ 动态补全缓存（比赛/题目 id + 描述列）
8. ✅ 子 shell 注入（bash/zsh/fish）
9. ✅ `autoplay`
10. 🚧 打磨：README 已补全；`help <命令>` 可用；交互式 register 完成
11. ✅ `admin` 命令集（bulletin/user/game/challenge/platform/cluster）+ 输出符号约定（✓/➜/✗/!）全量统一

### 已知待办
- `challenge solve`（`GET .../solves` 解题状态）未接入命令
- 子 shell 三种 shell 的真实环境冒烟（本机仅 zsh 可用）
