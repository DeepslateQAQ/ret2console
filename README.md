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

若需要构建出单二进制文件分发版本，可使用 Nuitka 进行构建，命令如下：

```bash
$ uv run --group build nuitka \
    --onefile --assume-yes-for-downloads \
    --no-deployment-flag=self-execution \
    --output-filename=dist/ret2console \
    --remove-output --jobs="$(nproc)" \
    src/ret2console/cli.py
```

构建出的产物位于 `dist/ret2console` 内。

建议的构建环境为 Ubuntu 22.04，其 glibc 版本为 2.35，支持大部分 glibc 版本高于 2.34 的发行版。

您也可以使用容器在可控环境内进行构建：

```bash
$ ./scripts/build-in-container.sh
```

如需重建容器环境（例如换构建基线镜像后）可加 `--cleanup` 参数。

## 相关项目

 - [Ret2Shell](https://github.com/Ret2Shell/Ret2Shell) - 特性丰富的夺旗赛平台
 - [ret2cli](https://github.com/LyCecilion/ret2cli) - Ret2Shell CTF 平台的 CLI 客户端（第三方）

## 开源协议 / LICENSE

本项目基于 GNU General Public License V3 授权。
