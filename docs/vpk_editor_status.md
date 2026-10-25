# VPK Editor — Implementation Status

Last verified: 2026-10-25, Windows, `.venv` (Python 3.14, `vpk` 1.4.0).

This document distinguishes implemented and tested behavior from unverified or
unsupported functionality. It is not a claim of full Source 2 modding support.

## Implemented and verified

| Capability | Status | Evidence |
|---|---|---|
| Open/parse VPK v1 | Working | `test_vpk_editor_reader.py` (manually crafted v1 archive) |
| Open/parse VPK v2 | Working | `test_vpk_editor_reader.py`, `test_vpk_editor_builder.py` |
| Read embedded file data | Working | Reader tests, byte-for-byte assertions |
| Read split archive data (`*_dir.vpk` + `*_000.vpk`) | Working (v1 fixture) | `test_reads_split_archive` |
| Read preload data | Working | `test_reads_preload_bytes` |
| Detect missing split archive | Working | `test_missing_split_archive_is_reported` |
| CRC32 verification per resource | Working | `test_crc_mismatch_is_detected` |
| Reject corrupt/truncated archives | Working | `test_corrupt_magic_and_truncated_header` |
| Reject unsafe archive paths | Working | `test_unsafe_archive_path_is_rejected` |
| Isolated temporary workspace | Working | `test_vpk_editor_workspace.py`, source-hash test |
| Path traversal protection | Working | Workspace tests (`..`, absolute, drive, NUL, reserved names) |
| List/filter resources | Working | `test_list_resources_and_filters` |
| Hero alias resolution (`Haze`, `grey_talon`, `geist`…) | Working (roster-based) | `test_hero_and_ability_search` |
| Ability-3 sound candidate search | Working (path heuristic) | `test_hero_and_ability_search` |
| Replace existing `.vsnd_c` with another `.vsnd_c` | Working | `test_replace_build_and_preserve_source` |
| Raw replacement of any existing resource | Working | `test_generic_resource_replacement` |
| Build output VPK v1 | Working | `test_build_and_reread[1]` |
| Build output VPK v2 with checksums | Working | `test_build_and_reread[2]`; `vpk.VPK.verify()` is true |
| Read-back validation of output | Working | Builder reopens output and CRC-checks every entry |
| Atomic output + conflict handling | Working | Conflict/overwrite tests; existing output preserved on failure |
| Never overwrite source VPK | Working | `test_source_archive_cannot_be_overwritten`, API test |
| Source unchanged after edit/build | Working | SHA-256 comparison before/after |
| Close/cleanup workspace | Working | Close and context-manager tests |
| Thread-safe core (no Qt, no globals) | By design | No PySide6 imports; one workspace per project |
| Reuse existing tag analyzer | Working | `VPKProject.tags` → `core.vpk_parser.analyze_vpk` |
| GUI entry point (separate window) | Working | `ui/vpk_editor_window.py`, `tests/test_vpk_editor_window.py`; visible in `scripts/ui_preview.py` |
| Texture preview in the editor | Working | `.vtex_c` → PNG via `Source2Viewer-CLI.exe`; `tests/test_vpk_preview.py`; checked on `staging/violetoutfit` |
| Model preview in the editor | Working (needs internet) | `.vmdl_c` → GLB via CLI, shown by `ui/model_viewer.py` (`<model-viewer>` from CDN); checked on `staging/violetoutfit` |
| Text preview + expandable panel | Working | `test_vpk_preview.py`, `test_text_resource_gets_text_preview`, `test_preview_expand_toggles_resource_list` |

New test result at the time of writing: **62 passed, 1 skipped** (the skipped test
exercises case-sensitive filename collisions and cannot run on a
case-insensitive Windows filesystem). Relevant existing tests
(`test_vpk_parser.py`, `test_archiver.py`) also pass. A current full `.venv` run
reports **178 passed, 1 skipped**; the editor window has its own integration
tests (`test_vpk_editor_window.py`: open → filter → replace → build → preview)
and the preview helpers are covered by `test_vpk_preview.py`. See "Not verified"
for the GUI/network caveat.

## GUI integration

- `ui/vpk_editor_window.py` provides `VPKEditorWindow`, opened from the main
  header button «🧩 VPK-редактор» (collapses to an icon on narrow windows).
- Long operations run in `QThread` workers (`OpenVPKWorker`, `ReplaceWorker`,
  `BuildWorker`, `PreviewWorker`); the core package stays Qt-free.
- The window lists resources with a path search and a «только звуки» filter,
  replaces one selected resource, and builds a new `.vpk`. It never writes to
  the source archive; the build target is chosen in a save dialog and the
  existing output requires explicit confirmation.
- The right panel shows a preview of the selected resource: `.vtex_c` as an
  image, `.vmdl_c` in the shared 3D viewer, small text files as text, anything
  else with an explanatory message. The `⤢` button expands the panel over the
  whole window; the list/panel split can also be dragged.
- Closing the window (or the app) stops its workers and deletes the temporary
  workspace copy and the preview cache.

## Explicitly unsupported

### Real Source 2 audio compilation

- There is **no** working WAV/MP3/OGG/FLAC → `.vsnd_c` compilation.
- The default `UnavailableResourceCompiler` always raises
  `CompilationUnavailableError`.
- Tests use a mock compiler only to verify adapter wiring; this is **not**
  evidence of real compilation support.
- No external compiler exists in the repository; no executable is downloaded at
  runtime.
- A future integration may inject a `ResourceCompiler` implementation. Until
  then, the workflow can replace an existing sound only with a ready
  `.vsnd_c` file.

### Semantic ability mapping

- `find_ability_sounds()` matches hero tokens and `ability3`-style path segments.
- It does not parse compiled `.vsnd_c` contents, event scripts, or ability
  definitions. Multiple candidates are possible; the caller must choose.
- A file named like ability 3 is not proof that it is the third ability's sound.

### Other unsupported formats/variants

- VPK versions other than 1 and 2.
- Encrypted or otherwise non-standard VPK variants.
- Signed VPK metadata on write (v2 checksums are written; signatures are not).
- Files without an extension in the write path: the underlying `vpk` writer
  rejects them; the editor reports `UnsupportedResourceError` instead of
  producing an invalid archive.
- Sparse/huge-file streaming build: build verifies by rereading entries, so
  large mods incur a full extra read pass.

## Not verified

- Loading a built archive in Deadlock itself.
- Behavior inside PyInstaller `--onedir` (the module is pure Python, but the
  frozen executable was not rebuilt in this task).
- Real-world mod archives beyond synthetic fixtures. The reader handles v1/v2,
  preload and split archives, but the split-archive test uses a v1 fixture;
  v2 split archives follow the same index path but were not separately verified.
- Compatibility of a produced v1 archive with the current Deadlock build; v2 is
  the default and matches modern Source 2 packaging.
- Texture preview was checked on one real mod (`staging/violetoutfit`, a PNG
  texture); special texture types (cubemaps, arrays, TGA-sourced) were not
  exhaustively verified.
- 3D preview: GLB export was verified on a real mod, and the WebEngine page is
  created, but shaded rendering cannot be judged in offscreen screenshots — it
  uses the same `<model-viewer>` page as the main window.
- Full-suite stability with network enabled: GameBanana tests mock the network
  (autouse fixture), so no real requests are made. Current `.venv` run:
  178 passed, 1 skipped.

## Known constraints

- `vpk` 1.4.0 is required and is already listed in `requirements.txt`.
- Windows path rules are enforced even on other platforms (reserved names,
  trailing spaces/dots, case-insensitive collisions), because the target app is
  Windows-only.
- `read_resource()` loads a resource into memory; use `iter_chunks()` or
  `copy_resource()` for large files.
- Cancellation is not implemented inside a single read/build operation. A
  UI-level worker can stop between operations, but a build currently runs to
  completion or failure.
- Build output must go to an existing directory; the module does not create
  arbitrary parent folders.
- Preview conversions call `Source2Viewer-CLI.exe` synchronously inside a worker
  thread; model export can take seconds and is not cancellable mid-run (the
  preview thread is stopped only when the editor window is closed).

## Files added

- `core/vpk_editor/__init__.py`
- `core/vpk_editor/api.py`
- `core/vpk_editor/errors.py`
- `core/vpk_editor/models.py`
- `core/vpk_editor/pathing.py`
- `core/vpk_editor/resource_compiler.py`
- `core/vpk_editor/resource_inspector.py`
- `core/vpk_editor/sound_replacer.py`
- `core/vpk_editor/vpk_builder.py`
- `core/vpk_editor/vpk_reader.py`
- `core/vpk_editor/workspace.py`
- `tests/vpk_editor_fixtures.py`
- `tests/test_vpk_editor_api.py`
- `tests/test_vpk_editor_builder.py`
- `tests/test_vpk_editor_reader.py`
- `tests/test_vpk_editor_sound_replacer.py`
- `tests/test_vpk_editor_workspace.py`
- `core/vpk_preview.py` (resource previews for the UI)
- `ui/model_viewer.py` (shared WebEngine model viewer)
- `ui/vpk_editor_window.py` (editor dialog + preview panel)
- `tests/test_vpk_preview.py`
- `tests/test_vpk_editor_window.py`
- `docs/vpk_editor_integration.md`
- `docs/vpk_editor_status.md`
