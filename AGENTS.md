# Repository‑Specific Guidance

## Context & Scope
- Read `PROJECT_MAP.md` only when you need orientation; don't load it for trivial edits and don't duplicate it here.
- Start from the smallest scope that answers the question: the symbol, then the file, then its tests, then related modules.
- Use symbol/`grep` lookups and targeted excerpts instead of reading whole files; read a full file only when the change genuinely spans it.
- Don't re-read files or repeat searches whose result is already in the current context. Reuse verified findings.
- Before widening the search, state the unanswered question that requires more evidence.
- Extract findings from large tool output instead of pasting raw dumps; never explore `.venv/`, `build/`, `dist/`, `__pycache__/`, `.serena/cache/`.
- Still inspect enough implementation and tests to change behavior safely — token savings never justify a risky guess.

## Quick Start
- **Run the GUI**: `python main.py` (from repo root; entry point is `main.py`).
- **Run tests**: `pytest -q` — test files live under `tests/`; fully mocked (no real network), fast (~1s; baseline 79 passed, 1 skipped). GUI tests need PySide6 (else skipped via `importorskip`).
- **Check syntax**: `python -m compileall -q core ui main.py build.py`.
- **Build an executable**: `python build.py` → Windows‑only onefile `dist/DeadlockModManager.exe` (PyInstaller). Needs `unrar.exe` + `vpkmerge.exe` in repo root or `_internal`; warns if missing. `DeadlockModManager.spec` is a stale generated artifact with absolute paths — `build.py` does not use it.

## Layout & Boundaries
- `core/` — pure logic, no CLI entry points: GameBanana API client (`gamebanana.py`), deploy (`deployer.py`), gameinfo patching (`gameinfo.py`), VPK healing (`vpk_fixer.py`), archiving/staging (`archiver.py`, `jsonio`).
- `ui/` — PySide6 GUI: `main_window.py` (main window + worker threads), `gamebanana_window.py` (mod browser), `widgets.py`, `styles.py`.
- `staging/` — mods awaiting deploy; each folder has a `meta.json` (`name`, `tags_data.{heroes,types}`, `enabled`, `is_stale`, `stale_models`) and a `content.vpk`.

## Development Workflow
1. Change code in `core/` or `ui/`; run `pytest -q` then `ruff check .` before committing. Note: ruff (0.17.1, default config) currently reports 60 pre‑existing findings; don't assume all are from your change and don't mass‑fix unrelated files.
2. After adding a feature that touches mod metadata, update the relevant `meta.json` schema in `staging/` and re‑run `TagsWorker` via the GUI (it backfills tags for stale `meta.json`).

## Common Gotchas
- **Python 3.10+** required (code uses PEP 604/585 syntax); the local interpreter is CPython 3.12.10 on PATH.
- **Windows‑only binaries** `unrar.exe`, `vpkmerge.exe` must sit in the repo root (or `_internal`) for PyInstaller builds; `build.py` warns if missing.
- **File locks**: no other process may lock `staging/` files while tests run, or `jsonio.write_json_atomic` fails.
- **Do not delete `config.json`** — it holds `game_path` (must point to a folder containing `gameinfo.gi`). If the saved path is invalid, the GUI auto‑detects via Steam registry / `libraryfolders.vdf`.

## Core Operations (GUI‑triggered, no CLI)
Core modules have no `__main__`; the operations below are wired to GUI buttons in `ui/main_window.py`. Call them programmatically by importing and passing `game_path` from `config.json`:
- **Apply mods** (⚡ Применить моды): `deployer.deploy_active_mods(game_path, progress_callback)` → `(total, err)`. Deploys enabled mods as `pakXX_dir.vpk` in `<game_path>/addons`; deletes only files it owns; rolls back on failure; state tracked in `deployed_state.json`. **Game must be closed** — a running client locks the addons pak files and deploy fails with "Закройте игру".
- **Patch gameinfo**: `gameinfo.patch_gameinfo_file(game_path)` → `(ok, message)`. Creates `gameinfo.gi.bak`; refuses if multiple `SearchPaths` blocks exist (ambiguous).
- **Heal a mod VPK**: `vpk_fixer.heal_mod_vpk(mod_folder, game_path)` → `(ok, message)`. Needs `vpkmerge.exe` + original `pak01_dir.vpk` in the game folder; creates `content.vpk.bak` / `content.vpk.orig.bak` backups.
- **Scan mods** (🔍 Проверить моды): runs `vpkmerge attachments check` against each staged mod's `content.vpk`; needs `vpkmerge.exe` + base pak.

## Staging / Import rules
- Supported extensions: `.vpk`, `.zip`, `.7z`, `.rar` (`VALID_EXTS`). RAR → `unrar.exe` (via rarfile) or Windows `tar.exe` fallback; 7z → `py7zr`.
- Re‑importing a mod with the same name updates the existing folder and **keeps `enabled`**; different mods get `_1`, `_2` suffixes. Folder names are sanitized to alnum/space/underscore/hyphen (empty → `mod`).

## Language convention
- UI strings, status messages, and docstrings are in **Russian** — keep new user‑facing text in Russian.

## MCP Server (Serena)
- Provider config is global (`~/.config/opencode/opencode.jsonc`), not this repo. `mcp_servers/serena.json` here is a reference snippet only — neither the app nor OpenCode reads it.
- Serena project config lives in `.serena/project.yml` (Python LSP, `ignore_all_files_in_gitignore: true`); `.serena/memories/` is empty and `.serena/cache/` only holds LSP cache (don't edit either).
- Serena is exposed through a 2-tool REPL (`initial_instructions`, `serena_repl`): call `initial_instructions` first, then use symbol/`find_symbol`-style APIs inside the REPL when a Python symbol lookup beats grep.
- Manual start: `uvx --from git+https://github.com/oraios/serena serena start-mcp-server --context ide-assistant --project <repo>` (use the Windows path, not `$(pwd)`).


---
This file is intentionally concise; refer to the code comments and docstrings in `core/` for deeper details.
