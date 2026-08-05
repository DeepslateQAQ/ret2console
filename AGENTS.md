# Repository Guidelines

## Project Overview

Ret2Console is a Python terminal client for the Ret2Shell CTF platform. It provides an interactive REPL, one-shot commands, script execution, a read-only API-backed virtual filesystem, challenge instance tunnels, downloads, and flag submission. `SPEC.md` is the design authority; update it when intended behavior changes or when implementation and specification disagree.

## Architecture & Data Flow

1. `ret2console`, `r2c`, or `python -m ret2console` enters `ret2console.cli:main`.
2. `src/ret2console/cli.py` registers command families, builds the central `App`, and selects REPL, `-c`, or script mode.
3. `src/ret2console/repl.py` parses `;`/`&&`, recognizes pasted flags, resolves `commands.REGISTRY`, and awaits a command handler.
4. Handlers in `src/ret2console/commands/` read or update `App`/`Context`, then call the API, VFS, tunnel, subprocess, or rendering layer. Handlers return shell-like integer statuses: normally `0` success, `1` expected failure, and `127` unknown/unavailable command.
5. `src/ret2console/api/client.py` owns the lazy `httpx.AsyncClient`, bearer token renewal/invalidation, error normalization, and pagination.
6. `src/ret2console/vfs_builder.py` binds lazy API loaders to `Vfs` nodes. Navigation updates `Context.vfs_path`, `game_id`, and `challenge_id`; completion reuses populated caches.
7. `src/ret2console/wsrx.py` owns loopback challenge tunnels for the `App` lifetime. CLI shutdown closes tunnels and HTTP resources in `finally`.

State is centralized in the `App` dataclass (`src/ret2console/state.py`) and passed to handlers rather than hidden behind service globals. The command registry and shared Rich consoles are the notable globals. Profile switching resets transport, VFS context, and caches as one state transition.

## Key Directories

- `src/ret2console/`: application package and terminal/UI helpers.
- `src/ret2console/api/`: async Ret2Shell HTTP client and proof-of-work support.
- `src/ret2console/commands/`: command handlers grouped by domain (`auth.py`, `challenge.py`, `submit.py`, `admin.py`, etc.).
- `tests/`: flat pytest suite organized by feature; no separate unit/integration tree.
- `scripts/`: native, Windows, and containerized Nuitka one-file builds.
- `.github/workflows/`: multi-platform Nuitka build and binary smoke checks.

## Development Commands

```bash
uv sync                    # create/update the development environment
uv run r2c                 # interactive client
uv run r2c -c "pwd; status" # one-shot command sequence
uv run pytest              # full test suite
uv run pytest tests/test_vfs.py::test_resolve_nested  # focused test
```

Build commands:

```bash
scripts/build.sh                         # native Linux/macOS; dependencies preinstalled
scripts/build-in-container.sh            # Ubuntu 22.04 Docker build
scripts/build-in-container.sh --cleanup  # recreate the builder container
powershell -ExecutionPolicy Bypass -File scripts/build.ps1  # Windows
scripts\build.bat                         # Windows cmd wrapper
```

Native outputs are under `build/{linux,macos,win}/`; the container build emits `dist/ret2console`. The repository defines no lint, formatter, static-type-check, or coverage command. Do not invent one.

## Code Conventions & Common Patterns

- Use `snake_case` for modules/functions, `PascalCase` for dataclasses/types, `_leading_underscore` for private helpers, and `UPPER_CASE` for constants.
- Command handlers are async functions named `cmd_<domain>_<action>` and return `int`. Each domain exposes `register_<domain>()`; add it to `_register_all()` in `cli.py`.
- Compound commands use explicit subcommand-to-coroutine dictionaries, not class hierarchies.
- Keep I/O async: HTTP, prompts, VFS loaders/readers, subprocesses, polling, and tunnel lifecycle are awaited. Use synchronous helpers for parsing, formatting, and local state changes. Use `asyncio.create_task` only for genuine overlap with explicit lifecycle handling.
- Inject operational dependencies through `App`. Follow existing concrete owners (`ApiClient`, `Vfs`, `Wsrx`) rather than adding a second dependency-injection scheme.
- Normalize inconsistent backend payloads at the API or feature boundary. API payloads are commonly `dict[str, Any]`; do not spread response-shape branching through UI code.
- Raise `ApiError` for transport/backend failures and `VfsError` for virtual-path failures. Expected command errors are rendered for the user (messages are generally Chinese) and return `1`; do not terminate the REPL.
- Preserve cleanup invariants: API and tunnel owners close on every exit path, and partially created tunnel sets roll back on failure.
- Preserve privacy behavior in `src/ret2console/flag.py` and `FilteredFileHistory`: flag-shaped input must not enter history.
- Persistence belongs under `~/.ret2console/`; session files require mode `0600`. Tests redirect this state to `.test-home/`.

## Important Files

- `SPEC.md`: sole design/behavior reference, including commands, persistence, API behavior, and known gaps.
- `pyproject.toml`: package metadata, Python floor, dependencies, entry points, Hatchling and pytest configuration.
- `uv.lock`: committed reproducible dependency lock; keep it synchronized with dependency changes.
- `src/ret2console/cli.py`: composition root and lifecycle.
- `src/ret2console/state.py`: central mutable application/context model.
- `src/ret2console/repl.py`: input dispatch, conditional chaining, flag auto-submit, and interactive loop.
- `src/ret2console/commands/__init__.py`: `Command` contract and global registry.
- `src/ret2console/api/client.py`: HTTP boundary, authentication, token rotation, and pagination.
- `src/ret2console/vfs.py` / `vfs_builder.py`: lazy virtual filesystem and API bindings.
- `src/ret2console/config.py`: profiles, sessions, migration, and persistent paths.
- `src/ret2console/wsrx.py`: in-process challenge tunnel ownership.
- `tests/conftest.py`: isolated HOME/XDG state, command registration, event loop, and autouse cleanup.

## Runtime/Tooling Preferences

- Runtime: CPython `>=3.11`. Native binary scripts require Python 3.12+ and platform compiler/development tools.
- Package/environment manager: `uv`; use the committed `uv.lock`. Windows build scripts allow pip only as a fallback.
- Packaging: Hatchling with a `src/` layout. Development execution should use `uv run ...` rather than manipulating `PYTHONPATH`.
- Distribution: Nuitka one-file binaries. Linux native builds require `patchelf`; macOS uses `install_name_tool`; Windows uses Nuitka MinGW64.
- Nix packages/apps support only `x86_64-linux` and `aarch64-linux`.
- Runtime libraries have distinct roles: prompt-toolkit for input/completion, Rich for output, httpx for async HTTP, Pillow for terminal images, and libwsrx for local tunnels.

## Version Control

- Keep each commit focused on one coherent change. Do not mix unrelated user work into the commit.
- Before committing, inspect the working tree and stage only files belonging to the current task. Never discard, overwrite, or reformat unrelated changes.
- Use Gitmoji for every commit subject, formatted as `<code> <concise imperative subject>` (for example, `:memo: Document Gitmoji commit convention`). Choose the single Gitmoji code from https://gitmoji.dev/ that best represents the change; use its `:shortcode:` form, not the Unicode emoji. Add a body only when the rationale or risk is not evident from the diff.
- Run the relevant verification before committing and report both the commit hash and verification result.

## Testing & QA

- Frameworks: pytest and pytest-asyncio. Async tests use `@pytest.mark.asyncio`; tables use `@pytest.mark.parametrize`.
- Name files `tests/test_<feature>.py` and tests `test_<behavior>`. Keep feature-specific helpers and fake backends local and underscore-prefixed.
- Prefer `httpx.MockTransport` for API contracts, `monkeypatch` for environment/process/time boundaries, `tmp_path` for generated files, and `capsys` for terminal output.
- Assert observable contracts: return codes, state transitions, HTTP method/path/body, output, exact files/bytes, subprocess argv/env, and cleanup behavior. Avoid source-text and implementation-plumbing assertions.
- `tests/test_integration.py` covers in-process login-to-submission and persisted-session flows. `tests/test_wsrx.py` contains the real loopback TCP/WebSocket lifecycle test. Tests must not require live Ret2Shell services.
- The suite writes isolated state beneath `.test-home/`; do not bypass `tests/conftest.py` persistence cleanup.
- CI currently builds and smoke-tests binaries but does not run pytest or enforce coverage. Run `uv run pytest` locally for code changes and add focused tests for new observable behavior.
