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
import tempfile
from pathlib import Path

from ret2console.output import console
from ret2console.state import App

# 与 flag.py 保持一致（rc 脚本内嵌正则用）
# 注意：zsh 的 =~ 不接受 `\{` 转义（Invalid content of \{\}），
# 字面花括号用字符类 `[{]` / `[}]` 表示，bash/fish 同样兼容。
_FLAG_RE = r"^[A-Za-z0-9_]+[{][^{}]+[}]$"

# 提交通道：stdin 喂 flag
_SUBMIT_CMD = "ret2console -c 'submit -'"

SHELLS = ("bash", "zsh", "fish")


def installed(name: str) -> str | None:
    return shutil.which(name)


def _bash_rc() -> str:
    return f'''# ret2console 注入（bash）
submit() {{
    if [ $# -ge 1 ]; then
        printf '%s' "$1" | {_SUBMIT_CMD}
    else
        printf '用法: submit <flag>\\n'
    fi
}}
__r2c_submit_check() {{
    if [[ "$BASH_COMMAND" =~ {_FLAG_RE} ]]; then
        printf '%s' "$BASH_COMMAND" | {_SUBMIT_CMD}
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


def _zsh_rc() -> str:
    return f'''# ret2console 注入（zsh）
submit() {{
    if [ $# -ge 1 ]; then
        print -rn -- "$1" | {_SUBMIT_CMD}
    else
        print '用法: submit <flag>'
    fi
}}
autoload -Uz add-zsh-hook
__r2c_preexec() {{
    emulate -L zsh
    if [[ "$1" =~ {_FLAG_RE} ]]; then
        print -rn -- "$1" | {_SUBMIT_CMD}
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


def _fish_conf() -> str:
    return f'''# ret2console 注入（fish）
function submit
    if test (count $argv) -ge 1
        printf '%s' $argv[1] | {_SUBMIT_CMD}
    else
        echo '用法: submit <flag>'
    end
end
function __r2c_preexec --on-event fish_preexec
    set -l cmdline (commandline)
    if string match -qr '{_FLAG_RE}' -- "$cmdline"
        printf '%s' "$cmdline" | {_SUBMIT_CMD}
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
    else:  # fish
        env["XDG_CONFIG_HOME"] = str(tmpdir / "xconfig")
    return env


async def launch_shell(app: App, name: str, binary: str | None = None) -> int:
    binary = binary or installed(name)
    if binary is None:
        console.print(f"[red]✗ {name}: 未检测到本机安装[/red]")
        return 127
    with tempfile.TemporaryDirectory(prefix="r2c-shell-") as td:
        tmpdir = Path(td)
        args = [binary]
        env = _launch_env(name, tmpdir, app.ctx.game_id, app.ctx.challenge_id)
        if name == "bash":
            rc = tmpdir / "r2c-rc"
            rc.write_text(_bash_rc(), encoding="utf-8")
            args.append("--rcfile")
            args.append(str(rc))
        elif name == "zsh":
            (tmpdir / ".zshrc").write_text(_zsh_rc(), encoding="utf-8")
        else:  # fish
            conf = tmpdir / "xconfig" / "fish" / "conf.d"
            conf.mkdir(parents=True)
            (conf / "r2c-submit.fish").write_text(_fish_conf(), encoding="utf-8")
        console.print(f"[dim]已注入 flag 提交 handler（输入 {name} 退出后返回 ret2console）[/dim]")
        proc = await asyncio.create_subprocess_exec(*args, env=env)
        return await proc.wait()
    # 退出码语义：shell 退出码透传
