# $\texttt{\huge{\textcolor{red}{R}et\textcolor{gray}{2}\textcolor{blue}{C}onsole}}$

适用于 [Ret2Shell](https://github.com/Ret2Shell/Ret2Shell) 的命令行风格用户接口。

## 使用

Ret2Console 使用 uv 管理，你可以直接使用 uv 启动 Ret2Console：

```bash
$ uv run r2c
```

使用 `auth login` 进行登录。默认登录平台为 [西电 CTF 终端](https://ctf.xidian.edu.cn)。

您也可以使用其他平台，仅需使用命令 `auth endpoint https://your-ret2shell.domain/api` 即可。

多 profile：每个 profile 独立保存凭证与 API endpoint，默认使用 `default`。
`auth profile create work` 新建并切换，`auth profile use <名称>` 切换，
`auth profile list` 查看全部，`auth profile delete <名称>` 删除（最后一个 profile 不可删，
`default` 删除后下次 `use default` 会重新创建）。

登录、启动或切换 profile 时会自动检测已加入队伍且在比赛时间内的赛事，并询问是否快速切换。

具体使用方式可以参见 `help` 命令的内容。

---

为带来更快捷的操作体验，我们实现了一个虚拟文件系统来与平台进行交互，其结构大致类似于：

```
/
├── notice                      # 全局公告
└── contests/
    └── <比赛 ID>/
        ├── info                # 比赛信息
        ├── notice              # 比赛通知
        ├── scoreboard          # 排行榜
        ├── docs/               # readme / training / rules
        ├── team                # 队伍信息
        └── challenges/
            └── <题目 ID>/
                ├── README      # 题面（Markdown 渲染）
                ├── hint        # 题目提示
                ├── files/      # 附件（用 download 下载）
                ├── instance    # 靶机实例信息
                └── flag        # flag 提交
```

例如，进入比赛、阅读题面、启动实例并提交 flag：

```bash
$ cd /contests/22/challenges/908
$ cat README
$ challenge start 908
$ challenge connect        # 经 wsrx 映射本地端口
$ moectf{...}              # 直接粘贴 flag 即可提交
```

同时，Ret2Console 也实现了较为完整的 CLI 接口，因此你可以像使用 Bash / Zsh 一样使用 Ret2Console：

```bash
$ uv run r2c -c "...; ..."
```

或者放在 Shebang 里面使用：

```bash
#!/path/to/r2c
...
```

也可以像 GitHub CLI 一样直接执行单条命令（`r2c <命令> ...`，执行后退出）：

```bash
$ uv run r2c contest list --type game
$ uv run r2c submissions
$ uv run r2c auth show
$ uv run r2c auth edit --description "打 CTF 真开心"
$ uv run r2c completion bash        # 生成补全脚本（zsh / fish 同理）
```

### MCP（供 Agent 接入）

Ret2Console 内置 MCP stdio 服务，可被 Claude Code / Codex / OpenCode / omp 等 Agent 直接拉起：

```bash
$ r2c auth login        # 先登录（MCP 复用当前 profile 的会话）
$ r2c mcp serve         # stdio 服务，由 Agent 进程拉起
$ r2c mcp setup         # 选择 Agent，打印配置片段（不写入任何文件）
```

`r2c mcp serve` 的 stdout 只输出 MCP 报文，可用 `--profile` / `--url` / `--token` 临时覆盖凭据（也可用 `R2C_PROFILE` / `R2C_API_BASE` / `R2C_TOKEN` 环境变量）。`r2c mcp tools` 列出全部工具，`r2c mcp status` 查看服务配置与登录态。MCP 只暴露选手侧能力，不含 admin、autoplay 与子 shell。

### Nix 用户请看这里

Ret2Console 具有 Nix Flakes 支持，你可以直接使用 `nix run` 启动 Ret2Console：

```bash
$ nix run github:AstralFlare-owo/ret2console
```

其余操作方式同上。

## 开发

本项目由 Deepseek-V4-Flash-0731 完成。

项目的设计说明位于 [SPEC.md](SPEC.md)，可提供给 Agent 进行参考。

环境依赖使用 uv 进行管理，您可运行以下命令一键配置环境：

```bash
$ uv sync
```

### 构建

> [!WARNING]
>
> 我们的构建脚本未提供自动设置依赖功能，请在构建前运行以下命令设置依赖：
>
> **Linux / Darwin**（需 `python3.12+` 开发头文件、`uv` 或 `pip`、`patchelf`、`gcc`/`clang`）：
>
> ```bash
> # Ubuntu / Debian
> $ sudo apt install python3-dev patchelf gcc
> # Fedora
> $ sudo dnf install python3-devel patchelf gcc
> # Arch
> $ sudo pacman -S python patchelf gcc
> # macOS
> $ brew install python patchelf
> # 项目依赖（uv 或 pip 任选其一）
> $ uv sync --all-groups
> # 或：pip install -e . nuitka zstandard
> ```
>
> **Windows**（需 Windows 版 Python 3.12 + `uv`）：
>
> ```powershell
> $ uv sync --all-groups
> ```

对于不同的目标平台，您可以在特定的构建环境使用命令进行构建（Nuitka onefile 单文件产物）：

 - Windows: `powershell -ExecutionPolicy Bypass -File scripts/build.ps1`（或 `scripts\build.bat`）→ `build/win/ret2console.exe`
 - Linux / macOS: `scripts/build.sh` → `build/linux/ret2console` / `build/macos/ret2console`

此外，Linux 构建也可以使用 Docker 进行构建：

```bash
$ ./scripts/build-in-container.sh
```

这将自动以 Ubuntu 22.04 (glibc 2.35) 创建 `r2c-builder` 容器并在其中完成构建，并将构建产物导出到 `dist/ret2console`。

如需重建容器环境（例如换构建基线镜像后）可加 `--cleanup` 参数。

CI（GitHub Actions）会在推送 / 手动触发时并行构建五个目标（linux-amd64 / linux-arm64 / windows-amd64 / darwin-amd64 / darwin-arm64），产物以 `ret2console-nuitka-<target>` 命名上传为 artifact。

## 相关项目

 - [Ret2Shell](https://github.com/Ret2Shell/Ret2Shell) - 特性丰富的夺旗赛平台
 - [ret2cli](https://github.com/LyCecilion/ret2cli) - Ret2Shell CTF 平台的 CLI 客户端（第三方）

## 开源协议 / LICENSE

本项目基于 GNU General Public License V3 授权。
