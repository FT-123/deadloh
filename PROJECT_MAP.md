# PROJECT_MAP

On-demand navigation map. Verified 2026-10-24 against the working tree. Prefer targeted
symbol reads over full-file reads; treat this file as a starting index, not a spec.

## Overview
Windows-only PySide6 desktop app (Russian UI) that imports Deadlock mods into a local
`staging/` store, tags them from VPK contents, deploys them into the game's `addons/`
as `pakXX_dir.vpk`, patches `gameinfo.gi`, and browses/downloads mods from GameBanana.
~2.9k LOC Python. No CLI in `core/`; all operations are wired to GUI buttons.

## Entry points
- `main.py` — `QApplication` → `ui.main_window.DeadlockModManager`; inserts repo root into `sys.path`.
- `build.py` — regenerates `app_icon.ico`, then runs PyInstaller CLI (`--onefile`, `--noconsole`) → `dist/DeadlockModManager.exe`.
- `core/*` — importable functions only, no `__main__`.

## Structure
| Path | Responsibility | Key symbols |
|---|---|---|
| `core/paths.py` | Paths: `BASE_DIR`, `APP_DATA_DIR`, `STAGING_DIR`, `CONFIG_PATH`, `DEPLOYED_STATE_PATH` | frozen → `%APPDATA%\DeadlockModManager` |
| `core/locator.py` | Steam registry / `libraryfolders.vdf` lookup of `game/citadel` | `detect_deadlock_path` |
| `core/archiver.py` | Import `.vpk/.zip/.7z/.rar` into staging, folder naming, `meta.json` | `stage_vpk_file`, `extract_and_stage_archive`, `_safe_folder_name` |
| `core/vpk_parser.py` | Tags (`heroes`, `types`) from VPK entry names | `analyze_vpk` |
| `core/deployer.py` | Copy enabled mods to `<game>/addons` as `pakNN_dir.vpk`, rollback, state file | `deploy_active_mods`, `get_deployed_mods_set`, `_known_files` |
| `core/gameinfo.py` | Detect/patch `SearchPaths` block in `gameinfo.gi` (brace matching, comments, CRLF) | `patch_gameinfo_file`, `is_gameinfo_patched` |
| `core/vpk_fixer.py` | Stale-model check and VPK heal via external `vpkmerge.exe` | `check_mod_stale`, `heal_mod_vpk`, `get_base_pak` |
| `core/gamebanana.py` | GameBanana API client (search, popular, files, downloads) | `search_mods`, `get_popular_mods`, `download_file_bytes` |
| `core/constants.py` | App id, `VALID_EXTS`, hero alias map, `SEARCH_PATHS_TEMPLATE` | `HERO_MAP`, `HEROES` |
| `core/jsonio.py` | Atomic UTF-8 JSON write | `write_json_atomic` |
| `ui/main_window.py` | Main window, animated header, mod list/cards, quick filters, worker threads | `DeadlockModManager`, `ImportWorker`, `DeployWorker`, `ScanStaleWorker`, `HealWorker`, `TagsWorker` |
| `ui/gamebanana_window.py` | Mod browser dialog: popular grid, search, details, download | `GameBananaWindow`, `GBSearchWorker`, `GBDownloadWorker`, `GBImageLoader`, `GBPageFetchWorker` |
| `ui/widgets.py` | Reusable widgets (Gemini-style) | `ModCardWidget`, `ToggleSwitch`, `DropListWidget`, `AuroraHeader`, `SparkleMark`, `StatBadge`, `Toast`, `ElidedLabel` |
| `ui/styles.py` + `ui/style.qss` | QSS loading, palette, Windows dark titlebar, icon drawing | `load_stylesheet`, `refresh_style`, `apply_dark_titlebar`, `create_app_icon` |
| `tests/` | 8 pytest modules, fully mocked | see below |

## Dependency flow
`ui.main_window` → `core.{archiver, deployer, gameinfo, locator, vpk_fixer, vpk_parser, paths, constants}`.
`ui.gamebanana_window` → `core.{gamebanana, archiver, constants}`, `ui.styles`.
`core.archiver` → `core.{jsonio, paths, vpk_parser}`; `core.deployer` → `core.paths`;
`core.vpk_parser` → `core.constants`; `core.vpk_fixer` → `core.paths`.
`core` never imports `ui`.

## Runtime data
- `config.json` → `{"game_path": "...\\Deadlock\\game\\citadel"}` (folder must contain `gameinfo.gi`). In dev it lives in repo root; frozen build uses `%APPDATA%\DeadlockModManager`.
- `staging/<mod>/` → `content.vpk` + `meta.json` (`name`, `tags_data.{heroes,types}`, `enabled`, optional `is_stale`, `stale_models`).
- `deployed_state.json` → `{"deployed": [folder...], "files": {folder: "pakNN_dir.vpk"}}`; legacy state without `files` is migrated by `_known_files`.
- Tag vocabulary (fixed order): `Model, Texture, UI, Sound, Voice, VFX`.

## Commands (verified locally)
- `python main.py` — run GUI.
- `pytest -q` — 79 passed, 1 skipped, ~0.8 s.
- `ruff check .` — 60 pre-existing findings (baseline, not caused by new work).
- `python -m compileall -q core ui main.py build.py` — syntax check.
- `python build.py` — Windows `.exe`; warns if `vpkmerge.exe` is missing.

## Where to change things
- Deploy order/ownership/rollback → `core/deployer.py` (`deploy_active_mods`, `_install`, `_write_state`).
- Import formats / folder collision rules → `core/archiver.py`.
- Tag inference → `core/vpk_parser.py` + `HERO_MAP` in `core/constants.py`.
- Gameinfo ordering rules → `core/gameinfo.py` (`_find_searchpaths_block`, `_match_brace`).
- GameBanana endpoints/paging → `core/gamebanana.py`.
- Button/worker wiring, list refresh, header status → `ui/main_window.py`.
- Browser layout/pagination/images → `ui/gamebanana_window.py`.
- Visual theme → `ui/style.qss`.

## Pitfalls / boundaries
- Windows-only helpers `unrar.exe`, `vpkmerge.exe` must sit in repo root or `_internal`; RAR also falls back to system WinRAR / `tar.exe`.
- Deploy fails while Deadlock is running (addons paks are locked).
- `patch_gameinfo_file` refuses ambiguous files (multiple `SearchPaths`); backup made once (`gameinfo.gi.bak`).
- Tests monkeypatch `STAGING_DIR` / `DEPLOYED_STATE_PATH` to `tmp_path`; they never touch the real game.
- `DeadlockModManager.spec` is a stale generated artifact with absolute `E:/` paths — `build.py` does not use it.
- Root `.gitignore` now excludes generated/runtime data (`build/`, `dist/`, `*.spec`, `.venv/`, caches, `staging/`, `config.json`, `deployed_state.json`). The repo still has zero commits.
- Do not edit generated dirs: `build/`, `dist/`, `.serena/cache/`, `.codexray/`, `__pycache__/`.

## Not verified
- Real-game deploy/heal against a live Deadlock install (no integration test).
- Live GameBanana API responses (all tests mocked).
- Frozen `.exe` runtime behavior (not rebuilt in this pass).
- No CI configuration exists.
