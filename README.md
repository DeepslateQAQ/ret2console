# $\texttt{\huge{\textcolor{red}{R}et\textcolor{gray}{2}\textcolor{blue}{C}onsole}}$

适用于 [Ret2Shell](https://github.com/Ret2Shell/Ret2Shell) 的命令行风格用户接口。

## 使用

Ret2Console 使用 uv 管理，你可以直接使用 uv 启动 Ret2Console：

```bash
$ uv run r2c
```

使用 `auth login` 进行登录。默认登录平台为 [西电 CTF 终端](https://ctf.xidian.edu.cn)。

您也可以使用其他平台，仅需使用命令 `auth endpoint https://your-ret2shell.domain/api` 即可。

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

## 相关项目

 - [Ret2Shell](https://github.com/Ret2Shell/Ret2Shell) - 特性丰富的夺旗赛平台
 - [ret2cli](https://github.com/LyCecilion/ret2cli) - Ret2Shell CTF 平台的 CLI 客户端（第三方）

## 开源协议 / LICENSE

本项目基于 GNU General Public License V3 授权。
