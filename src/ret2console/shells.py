"""子 shell 注入（SPEC §11）：REPL 内启动 bash/zsh/fish，注入 flag 提取 handler。

策略：临时 rc/配置目录 + `preexec` 钩子，每条命令执行前检查整行是否匹配
flag 格式，命中则经 `ret2console -c 'submit -'` 从 stdin 喂入提交
（零注入风险，flag 内含引号/分号/反引号均安全）。仅交互式启动注入。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from ret2console.output import console
from ret2console.state import App

# 与 flag.py 保持一致（rc 脚本内嵌正则用）
# 注意：zsh 的 =~ 不接受 `\{` 转义（Invalid content of \{\}），
# 字面花括号用字符类 `[{]` / `[}]` 表示，bash/fish 同样兼容。
_FLAG_RE = r"^[A-Za-z0-9_]+[{][^{}]+[}]$"

# 提交通道：stdin 喂 flag（占位符，launch_shell 时替换为实际可执行路径）
# 实际执行路径动态注入：onefile 用 sys.argv[0]；源码运行用 sys.executable。
# 不能硬编码 ret2console——用户可能未将 r2c 放入 PATH。
_SUBMIT_CMD = "__R2C_BIN__ -c 'submit -'"
_SUBMIT_PLACEHOLDER = "__R2C_BIN__"

# Windows shell 无 rc 注入机制（见 launch_shell），仅原生启动，不支持 flag 自动提交
WINDOWS_SHELLS = ("cmd", "pwsh", "powershell")
UNIX_SHELLS = ("bash", "zsh", "fish")


def shell_names() -> tuple[str, ...]:
    """当前平台可用的 shell 命令列表：Windows 下隐藏 bash/zsh/fish，换为 cmd/pwsh/powershell。"""
    return WINDOWS_SHELLS if sys.platform == "win32" else UNIX_SHELLS


def is_windows_shell(name: str) -> bool:
    return name in WINDOWS_SHELLS


def installed(name: str) -> str | None:
    if name in WINDOWS_SHELLS:
        if sys.platform != "win32":
            return None
        if name == "cmd":
            return "cmd.exe"  # Windows 系统组件，必然存在
        if name == "powershell":
            # Windows PowerShell 5.1：system32 系统组件，必然存在
            return "powershell.exe"
        return shutil.which(name)  # pwsh 需额外安装（PATH 或标准位置）
    return shutil.which(name)


def _shell_rc(name: str, submit_cmd: str) -> str:
    """生成注入 rc 内容（bash/zsh/fish 共用），提交通道为实际可执行路径。"""
    if name == "bash":
        return _bash_rc(submit_cmd)
    if name == "zsh":
        return _zsh_rc(submit_cmd)
    return _fish_conf(submit_cmd)


def _bash_rc(submit_cmd: str = "") -> str:
    cmd = submit_cmd or _SUBMIT_CMD
    return f'''# ret2console 注入（bash）
submit() {{
    if [ $# -ge 1 ]; then
        printf '%s' "$1" | {cmd}
    else
        printf '用法: submit <flag>\\n'
    fi
}}
__r2c_submit_check() {{
    if [[ "$BASH_COMMAND" =~ {_FLAG_RE} ]]; then
        printf '%s' "$BASH_COMMAND" | {cmd}
    fi
}}
trap __r2c_submit_check DEBUG

# 注入在前，source 用户自己的 rc；最后重装 DEBUG trap 防止被用户 rc 覆盖
if [ -f "$HOME/.bashrc" ]; then
    . "$HOME/.bashrc"
fi
trap __r2c_submit_check DEBUG

# 包装 command-not-found：flag 已由 DEBUG trap 提交，这里静默；
# 其余命令转交用户 rc / 系统已注册的 handler（如 Ubuntu 的 command-not-found）
if declare -F command_not_found_handle >/dev/null 2>&1; then
    eval "$(declare -f command_not_found_handle | sed '1s/command_not_found_handle/__r2c_cnf_orig/')"
else
    __r2c_cnf_orig() {{
        printf 'bash: %s: command not found\\n' "$1" >&2
        return 127
    }}
fi
command_not_found_handle() {{
    if [[ "$1" =~ {_FLAG_RE} ]]; then
        return 0
    fi
    __r2c_cnf_orig "$@"
}}
'''


def _zsh_rc(submit_cmd: str = "") -> str:
    cmd = submit_cmd or _SUBMIT_CMD
    return f'''# ret2console 注入（zsh）
submit() {{
    if [ $# -ge 1 ]; then
        print -rn -- "$1" | {cmd}
    else
        print '用法: submit <flag>'
    fi
}}
autoload -Uz add-zsh-hook
__r2c_preexec() {{
    emulate -L zsh
    if [[ "$1" =~ {_FLAG_RE} ]]; then
        print -rn -- "$1" | {cmd}
    fi
}}
add-zsh-hook preexec __r2c_preexec

# 注入在前，source 用户自己的 rc（ZDOTDIR 已被覆盖，指向原 ZDOTDIR 或 $HOME）
if [[ -f "${{R2C_ORIG_ZDOTDIR:-$HOME}}/.zshrc" ]]; then
    source "${{R2C_ORIG_ZDOTDIR:-$HOME}}/.zshrc"
fi

# 防御：用户 rc 可能清掉 preexec 钩子（如 add-zsh-hook -D preexec），先删后挂避免重复
add-zsh-hook -d preexec __r2c_preexec
add-zsh-hook preexec __r2c_preexec

# 包装 command-not-found：flag 已由 preexec 提交，这里静默；
# 其余命令转交用户 rc / 系统（如 NixOS、pkgfile）注册的 handler
if (( $+functions[command_not_found_handler] )); then
    functions -c command_not_found_handler __r2c_cnf_orig
else
    __r2c_cnf_orig() {{
        print -u2 -- "zsh: command not found: $1"
        return 127
    }}
fi
command_not_found_handler() {{
    if [[ "$1" =~ {_FLAG_RE} ]]; then
        return 0
    fi
    __r2c_cnf_orig "$@"
}}
'''


def _fish_conf(submit_cmd: str = "") -> str:
    cmd = submit_cmd or _SUBMIT_CMD
    return f'''# ret2console 注入（fish）
function submit
    if test (count $argv) -ge 1
        printf '%s' $argv[1] | {cmd}
    else
        echo '用法: submit <flag>'
    end
end
function __r2c_preexec --on-event fish_preexec
    set -l cmdline (commandline)
    if string match -qr '{_FLAG_RE}' -- "$cmdline"
        printf '%s' "$cmdline" | {cmd}
    end
end

# 注入在前，source 用户自己的配置（XDG_CONFIG_HOME 已被覆盖，指向原目录）
set -l r2c_orig "$R2C_ORIG_XDG_CONFIG_HOME"
if test -z "$r2c_orig"
    set r2c_orig "$HOME/.config"
end
for f in "$r2c_orig/fish/conf.d/"*.fish
    if test -f "$f"
        source "$f" 2>/dev/null
    end
end
if test -f "$r2c_orig/fish/config.fish"
    source "$r2c_orig/fish/config.fish" 2>/dev/null
end

# 包装 command-not-found：flag 已由 preexec 提交，这里静默；
# 其余命令转交用户注册的 fish_command_not_found
if functions -q fish_command_not_found
    functions -c fish_command_not_found __r2c_cnf_orig
else
    function __r2c_cnf_orig
        printf '%s\\n' "fish: Unknown command: $argv[1]" >&2
        return 127
    end
end
function fish_command_not_found
    if string match -qr '{_FLAG_RE}' -- "$argv[1]"
        return 0
    end
    __r2c_cnf_orig $argv
end
'''


def _ps_quote(path: str) -> str:
    """PowerShell 单引号字符串：包裹并转义内嵌单引号（''）。"""
    return "'" + path.replace("'", "''") + "'"


def _ps_profile(submit_cmd: str = "") -> str:
    """pwsh / powershell 注入脚本（PSReadLine Enter 钩子，等价 preexec）。

    PowerShell 无 command-not-found 钩子，但 PSReadLine 支持绑定 Enter 键
    在命令执行前拦截整行：命中 flag 格式则自动提交并丢弃该行。
    pwsh（7+）与 powershell（5.1）均内置 PSReadLine，同一脚本可用；
    两者 profile 路径不同，由 $PROFILE 自动区分（-NoProfile 启动后手动 source）。
    """
    cmd = submit_cmd or _SUBMIT_CMD
    return f'''# ret2console 注入（pwsh / powershell）
function submit {{
    param([string]$Flag)
    if ($Flag) {{
        $Flag | & {cmd}
    }} else {{
        Write-Host '用法: submit <flag>'
    }}
}}
$r2cHook = {{
    $r2cLine = $null
    $r2cCursor = $null
    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$r2cLine, [ref]$r2cCursor)
    $r2cLine = $r2cLine.Trim()
    if ($r2cLine -match '{_FLAG_RE}') {{
        $r2cLine | & {cmd}
        [Microsoft.PowerShell.PSConsoleReadLine]::RevertLine()
    }} else {{
        [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
    }}
}}
if (Get-Command Set-PSReadLineKeyHandler -ErrorAction SilentlyContinue) {{
    Set-PSReadLineKeyHandler -Key Enter -ScriptBlock $r2cHook
    # 注入在前，source 用户自己的 profile（-NoProfile 启动后手动恢复），随后重装钩子防覆盖
    foreach ($r2cP in @($PROFILE.AllUsersAllHosts, $PROFILE.AllUsersCurrentHost, $PROFILE.CurrentUserAllHosts, $PROFILE.CurrentUserCurrentHost)) {{
        if (Test-Path -LiteralPath $r2cP) {{ . $r2cP }}
    }}
    Set-PSReadLineKeyHandler -Key Enter -ScriptBlock $r2cHook
    Write-Host '已注入 flag 自动提交 handler'
}} else {{
    Write-Host '! 未检测到 PSReadLine：flag 自动提交不可用，请将 flag 复制回 ret2console 粘贴提交' -ForegroundColor Yellow
}}
'''


def _launch_env(name: str, tmpdir: Path, game_id: int | None, challenge_id: int | None) -> dict[str, str]:
    """注入 shell 的环境：把当前题目上下文带给 `ret2console -c 'submit -'` 新进程。

    子 shell 里的提交是独立进程，没有 REPL 的 cd 上下文，
    通过 R2C_GAME_ID / R2C_CHALLENGE_ID 传递（submit 命令会读取回填）。
    """
    env = os.environ.copy()
    if game_id is not None:
        env["R2C_GAME_ID"] = str(game_id)
        if challenge_id is not None:
            env["R2C_CHALLENGE_ID"] = str(challenge_id)
    # 保存用户原始配置路径，供注入 rc 在 hook 之后 source（R2C_ORIG_*）
    if "ZDOTDIR" in env:
        env["R2C_ORIG_ZDOTDIR"] = env["ZDOTDIR"]
    if "XDG_CONFIG_HOME" in env:
        env["R2C_ORIG_XDG_CONFIG_HOME"] = env["XDG_CONFIG_HOME"]
    if name == "bash":
        pass  # 交互式用 --rcfile 注入
    elif name == "zsh":
        env["ZDOTDIR"] = str(tmpdir)
    elif name == "fish":
        env["XDG_CONFIG_HOME"] = str(tmpdir / "xconfig")
    # cmd / pwsh / powershell：无 rc/配置目录注入，不改环境
    return env


async def launch_shell(app: App, name: str, binary: str | None = None) -> int:
    binary = binary or installed(name)
    if binary is None:
        console.print(f"[red]✗ {name}: 未检测到本机安装[/red]")
        return 127
    # 实际可执行文件：onefile 用 sys.argv[0]（打包后 argv[0] 即自身 exe）；源码运行用 sys.executable
    # 用户可能未把 r2c 放入 PATH，注入脚本里必须用绝对路径调起提交
    if getattr(sys, "frozen", False):
        r2c_bin = str(Path(sys.argv[0]).resolve())
    else:
        r2c_bin = str(Path(sys.executable).resolve())
    # 单引号转义：PowerShell 用 ''，Bash/Zsh/Fish 用 '\''
    ps_quoted = r2c_bin.replace("'", "''")
    sh_quoted = r2c_bin.replace("'", "'\\''")
    if getattr(sys, "frozen", False):
        ps_cmd = f"'{ps_quoted}' -c 'submit -'"
        unix_cmd = f"'{sh_quoted}' -c 'submit -'"
    else:
        # 源码运行：python.exe -m ret2console.cli（onefile 无 -m）
        ps_cmd = f"'{ps_quoted}' -m ret2console.cli -c 'submit -'"
        unix_cmd = f"'{sh_quoted}' -m ret2console.cli -c 'submit -'"
    if is_windows_shell(name):
        if name == "cmd":
            # cmd 无钩子机制：原生启动，不支持 flag 自动提交（用户明确仅警告）
            console.print(
                "[yellow]! cmd 下不支持 flag 自动提交：请将 flag 复制回 ret2console 粘贴提交[/yellow]"
            )
            proc = await asyncio.create_subprocess_exec(binary)
            return await proc.wait()
        # pwsh / powershell：PSReadLine Enter 钩子注入（-NoProfile 后手动 source 用户 profile）
        with tempfile.TemporaryDirectory(prefix="r2c-shell-") as td:
            tmpdir = Path(td)
            profile = tmpdir / "r2c-profile.ps1"
            # 5.1 以 ANSI 解析无 BOM 的 .ps1：UTF-8 中文会乱码（“宸叉敞鍏”）。
            # 写 UTF-8 BOM：5.1/7+ 均按 UTF-8 解析，中文正常。
            profile.write_bytes(b"\xef\xbb\xbf" + _ps_profile(ps_cmd).encode("utf-8"))
            console.print("[dim]已注入 flag 提交 handler（输入 exit 退出后返回 ret2console）[/dim]")
            # 把题目上下文（R2C_GAME_ID / R2C_CHALLENGE_ID）带给子 shell 内的 `submit -` 子进程
            env = _launch_env(name, tmpdir, app.ctx.game_id, app.ctx.challenge_id)
            # -ExecutionPolicy Bypass：注入脚本走临时文件，5.1 默认 Restricted 会拒跑（UnauthorizedAccess）
            if name == "powershell":
                # Windows PowerShell 5.1：-File 在非交互/重定向 stdin 下可能执行完脚本就退出，
                # 改用 -Command 点源脚本保持 -NoExit 交互
                cmd = f". {_ps_quote(str(profile))}"
                proc = await asyncio.create_subprocess_exec(
                    binary, "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", cmd, env=env
                )
            else:  # pwsh（PowerShell 7+）
                proc = await asyncio.create_subprocess_exec(
                    binary, "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", str(profile), env=env
                )
            return await proc.wait()
    with tempfile.TemporaryDirectory(prefix="r2c-shell-") as td:
        tmpdir = Path(td)
        args = [binary]
        env = _launch_env(name, tmpdir, app.ctx.game_id, app.ctx.challenge_id)
        rc = _shell_rc(name, unix_cmd)
        if name == "bash":
            rcfile = tmpdir / "r2c-rc"
            rcfile.write_text(rc, encoding="utf-8")
            args.append("--rcfile")
            args.append(str(rcfile))
        elif name == "zsh":
            (tmpdir / ".zshrc").write_text(rc, encoding="utf-8")
        else:  # fish
            conf = tmpdir / "xconfig" / "fish" / "conf.d"
            conf.mkdir(parents=True)
            (conf / "r2c-submit.fish").write_text(rc, encoding="utf-8")
        console.print(f"[dim]已注入 flag 提交 handler（输入 {name} 退出后返回 ret2console）[/dim]")
        proc = await asyncio.create_subprocess_exec(*args, env=env)
        return await proc.wait()
    # 退出码语义：shell 退出码透传
