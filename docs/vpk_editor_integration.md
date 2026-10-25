# VPK Editor Integration Guide

Isolated VPK editing module for Deadlock ModManager.

- Package: `core/vpk_editor/`
- Public entry point: `from core.vpk_editor import VPKEditor`
- UI: `ui/vpk_editor_window.py` (`VPKEditorWindow`, opened from the main header)
- Previews: `core/vpk_preview.py` (textures/models/text) + `ui/model_viewer.py` (shared 3D viewer)
- Tests: `tests/test_vpk_editor_*.py`, `tests/test_vpk_editor_window.py`, `tests/test_vpk_preview.py`
- Status and verified capabilities: [`vpk_editor_status.md`](vpk_editor_status.md)

The module is UI-independent: it imports no PySide6 symbols and can run inside a
`QThread`/background worker. It never writes to the source VPK, `gameinfo.gi`,
`staging/`, or `deployed_state.json`.

## Architecture

| File | Responsibility |
|---|---|
| `api.py` | `VPKEditor` / `VPKProject`: open, inspect, replace, build, close |
| `models.py` | `ResourceInfo`, `VPKArchiveInfo`, `ReplacementResult`, `BuildResult` |
| `errors.py` | Domain exceptions, all derived from `VPKEditorError` |
| `pathing.py` | Path normalization/validation shared by reader, workspace and builder |
| `vpk_reader.py` | VPK v1/v2 index, preload, split archives, CRC32-checked streaming |
| `workspace.py` | Temporary isolated copy, safe path resolution, atomic writes |
| `resource_inspector.py` | Hero/ability/sound heuristics using `HERO_MAP`/`HERO_ALIASES` |
| `sound_replacer.py` | Prepares `.vsnd_c` or compiled audio replacement |
| `resource_compiler.py` | Compiler protocol + honest "unavailable" implementation |
| `vpk_builder.py` | Atomic VPK v1/v2 build with read-back verification |

### Reuse of existing infrastructure

- `core.constants.HERO_MAP`, `HERO_ALIASES`, `HEROES` — hero resolution.
- `core.vpk_parser.analyze_vpk` — `VPKProject.tags` exposes the existing tag
  result; the editor does not duplicate tag inference.
- The `vpk` package already listed in `requirements.txt` and used by
  `core/vpk_parser.py` — container parsing/writing.
- `core.locator.detect_deadlock_path` is not called: editing a standalone mod
  VPK does not require the game directory. If future UI needs to verify an
  installed game, it should call the locator, not add a second discovery path.

## Public API

```python
from core.vpk_editor import VPKEditor

editor = VPKEditor()                      # optional: compiler=..., workspace_parent=...
project = editor.open_vpk("mod.vpk")      # index only; source is opened read-only

resources = project.list_resources()
sounds = project.find_ability_sounds("Haze", ability=3)

if sounds:
    project.replace_sound(
        target_resource=sounds[0].path,
        source_audio="custom.vsnd_c",     # or .wav with a real compiler
    )
    result = project.build("mod_edited.vpk")
    print(result.output_path, result.entry_count, result.verified)

project.close()                           # or use `with editor.open_vpk(...)`
```

Key methods:

- `list_resources(prefix=None, suffix=None, sounds_only=False)`
- `find_hero_sounds(hero)` / `find_ability_sounds(hero, ability=3)`
- `read_resource(path)` — CRC32 is verified.
- `replace_sound(target, source)` — `.vsnd_c` is copied byte-for-byte; WAV/MP3/
  OGG/FLAC goes through the configured compiler adapter.
- `replace_resource(target, source_file)` — raw byte replacement for any
  existing resource; no interpretation.
- `build(output, overwrite=False, version=2)` — never overwrites the source;
  output conflict is an error unless `overwrite=True`.
- `tags` — result of the existing `analyze_vpk`.
- `workspace_path` — temporary folder after first edit/build, otherwise `None`.

`VPKProject` is a context manager and is safe to use in a worker thread. Use one
project per worker; concurrent mutation of the same project is not supported.

## VPK support

Reading (verified by tests and a local probe):

- VPK v1 and v2.
- Embedded file data (`archive_index == 0x7FFF`), split archives
  (`pak01_dir.vpk` + `pak01_000.vpk`) and preload bytes. Split archives are
  verified with a v1 fixture; v2 uses the same index path but is not separately
  tested.
- CRC32 verification on every resource read.
- Rejects absolute paths, `..`, drive letters, NUL, Windows-reserved names,
  case-insensitive collisions, malformed headers and truncated data.

Writing (verified by tests):

- VPK v1 and v2 through `vpk.new()`. v2 archives include the MD5/checksum
  blocks and pass `vpk.VPK.verify()`.
- Entry paths/data are read back with this module's reader, CRC-verified, then
  the temporary output is atomically renamed into place.
- v1 is available for compatibility; v2 is the default.

Not verified: launching a built archive in Deadlock itself or inside the
PyInstaller onedir build (`dist/DeadlockModManager/`).

## Source 2 sound compilation — current limitation

The following are separate problems:

1. Reading/writing the VPK container — implemented.
2. Replacing an archive entry — implemented.
3. Compiling WAV/MP3/OGG/FLAC into Source 2 `.vsnd_c` — **not implemented here**.

The repository contains no Source 2 resource compiler, and the module does not
download arbitrary executables. By default:

- a `.vsnd_c` file can replace an existing `.vsnd_c` resource byte-for-byte;
- a `.wav`/`.mp3`/`.ogg`/`.flac` raises `CompilationUnavailableError` with a
  clear message.

Future integration may inject a real implementation of the `ResourceCompiler`
protocol:

```python
class MyCompiler:
    def compile_sound(self, source: Path, resource_path: str) -> bytes:
        ...  # call a licensed, locally installed compiler and return .vsnd_c bytes

editor = VPKEditor(compiler=MyCompiler())
```

This adapter is an integration point, not evidence of compilation support. Any
external tool must be obtained legally, distributed according to its license,
and verified against actual Deadlock resources before the UI offers it.

Ability detection (`find_ability_sounds`) is a path heuristic: it looks for hero
tokens and `ability3`/`ability_3`/`abilitythree` segments in resource paths. It
returns candidates; it does not inspect compiled resource semantics.

## Safety model

- The source archive is opened read-only; `build()` rejects output paths that
  resolve to the source file (including case-insensitive Windows paths).
- All edits happen in a `TemporaryDirectory` under the OS temp folder.
- Every resource path is normalized through `core/vpk_editor.pathing`; extraction
  and writes use `resolve_within()` and cannot escape the workspace.
- Output is written to a temporary file in the destination directory, verified
  by reopening and CRC-checking every entry, then `os.replace()`d atomically.
- On failure, temporary output is removed and an existing output file is left
  unchanged.
- Nothing is installed into the game, and no deployment state is touched.

## UI integration

The editor is wired into the app as a separate dialog
(`ui/vpk_editor_window.py` → `VPKEditorWindow`):

1. `ui/main_window.py` — header button «🧩 VPK-редактор» (`open_vpk_editor_window`)
   creates or raises the dialog; `closeEvent` calls its `shutdown()`.
2. Workers on top of the Qt-free core: `OpenVPKWorker` (`open_vpk` +
   `list_resources`), `ReplaceWorker` (`replace_sound` for `.vsnd_c` targets,
   `replace_resource` otherwise), `BuildWorker` (`build`). Only results/errors
   cross back to the GUI thread.
3. The dialog offers a resource list with path search and a «только звуки»
   filter, a details panel, replace and build actions, and a status line. Build
   output is chosen in a save dialog; overwriting an existing file requires
   confirmation, and the source path is rejected.
4. The right panel previews the selected resource through `core/vpk_preview.py`
   (`PreviewWorker`, one at a time, 250 ms debounce): `.vtex_c` is decompiled to
   PNG by `Source2Viewer-CLI.exe`, `.vmdl_c` is exported to GLB and shown by the
   shared `ui/model_viewer.py` (`<model-viewer>`, internet required), small text
   files are shown in a read-only editor, other types get an explanatory
   message. `⤢` expands the panel; the splitter can also be dragged.
5. `build.py` needs no extra binaries for this package: `vpk` is pure Python and
   is collected automatically by PyInstaller. Preview conversion reuses the
   already-bundled `Source2Viewer-CLI.exe`; without it, previews report a clear
   message instead of failing.

Still not implemented in the UI: WAV/MP3/OGG/FLAC compilation (see above),
hero/ability filters (`find_hero_sounds`/`find_ability_sounds` stay available
only through the Python API), audio playback/waveform preview, and
multi-resource batch replacement.

## Resource previews

`core/vpk_preview.py` is UI-independent and returns a `PreviewOutcome`:

| Kind | Meaning | Source |
|---|---|---|
| `image` | PNG path ready to display | `.vtex_c` decompiled with `Source2Viewer-CLI.exe -i <file.vtex_c> -o <png> -d` |
| `model` | GLB path ready for `<model-viewer>` | `Source2Viewer-CLI.exe -i <vpk> -o <dir> -d -e vmdl_c -f <entry> --gltf_export_format glb --gltf_textures_adapt --gltf_export_materials [--game gameinfo.gi]` |
| `text` | decoded text (up to 256 KB) | UTF-8/CP1251 heuristics |
| `none` | human-readable `message` | unsupported type, missing CLI, or conversion failure |

Previews are cached in a per-window temp directory: textures by content hash
(so a replaced texture previews the new bytes from the workspace), models by
VPK path + mtime + entry. The directory is deleted in `shutdown()`.

## Build / distribution notes

- The module relies only on the Python standard library and `vpk` (pure Python).
- Preview conversion shells out to the bundled `Source2Viewer-CLI.exe`; the
  editor itself does not download executables.
- The 3D preview loads `<model-viewer>` JS from the Google CDN at runtime, so it
  needs internet (same as the main window's model preview).
- `scripts/check.py --paths core/vpk_editor tests/test_vpk_editor_*.py` can be
  used for a scoped verification.
- Do not assume the existing `dist/DeadlockModManager.exe` contains this module
  until it is rebuilt with a UI import and verified in frozen mode.
