"""Вспомогательные генераторы синтетических VPK для тестов редактора."""

import struct
from binascii import crc32
from pathlib import Path, PurePosixPath

import vpk

VPK_SIGNATURE = 0x55AA1234


def write_tree(root: Path, files: dict[str, bytes]) -> Path:
    """Создаёт дерево файлов из отображения «относительный путь → байты»."""
    for relative, data in files.items():
        target = root / Path(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


def make_vpk(
    root: Path,
    files: dict[str, bytes],
    *,
    version: int = 2,
    name: str = "mod.vpk",
) -> Path:
    """Собирает настоящий VPK библиотекой vpk (v1 или v2)."""
    source = root / f"src-{name}"
    write_tree(source, files)
    writer = vpk.new(str(source))
    writer.version = version
    output = root / name
    writer.save(str(output))
    return output


def _group_entries(files: dict[str, bytes]):
    groups: dict[str, list[tuple[str, str, bytes]]] = {}
    for relative in sorted(files):
        posix = PurePosixPath(relative)
        parent = str(posix.parent)
        directory = "" if parent == "." else parent
        groups.setdefault(posix.suffix[1:], []).append(
            (directory, posix.stem, files[relative])
        )
    return groups


def make_manual_v1(
    path: Path,
    files: dict[str, bytes],
    *,
    archive_index: int = 0x7FFF,
    preloads: dict[str, bytes] | None = None,
) -> Path:
    """Пишет минимальный VPK v1 вручную (включая split-вариант и preload).

    Нужен для независимой проверки читателя: фикстура не использует тот же
    код записи, что и проверяемый модуль.
    """
    preloads = preloads or {}
    groups = _group_entries(files)
    records: list[tuple[str, str, str, bytes, int, bytes]] = []
    data_offset = 0
    for extension, entries in sorted(groups.items()):
        for directory, stem, data in sorted(entries):
            key = f"{directory}/{stem}.{extension}" if directory else f"{stem}.{extension}"
            preload = preloads.get(key, b"")
            records.append(
                (extension, directory, stem, data, data_offset, preload)
            )
            data_offset += len(data)

    tree = bytearray()
    for extension, entries in sorted(groups.items()):
        tree += extension.encode() + b"\x00"
        by_directory: dict[str, list[tuple[str, bytes, int, bytes]]] = {}
        for (
            record_extension,
            directory,
            stem,
            data,
            offset,
            preload,
        ) in records:
            if record_extension == extension:
                by_directory.setdefault(directory, []).append(
                    (stem, data, offset, preload)
                )
        for directory, items in by_directory.items():
            tree += (directory or " ").encode() + b"\x00"
            for stem, data, offset, preload in items:
                checksum = crc32(preload + data) & 0xFFFFFFFF
                tree += stem.encode() + b"\x00"
                tree += struct.pack(
                    "<IHHIIH",
                    checksum,
                    len(preload),
                    archive_index,
                    offset,
                    len(data),
                    0xFFFF,
                )
                tree += preload
            tree += b"\x00"
        tree += b"\x00"
    tree += b"\x00"

    archive_data = b"".join(
        record[3] for record in records
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(struct.pack("<III", VPK_SIGNATURE, 1, len(tree)))
        handle.write(tree)
        if archive_index == 0x7FFF:
            handle.write(archive_data)
        else:
            stem = path.stem
            base = stem[:-4] if stem.lower().endswith("_dir") else stem
            archive_name = f"{base}_{archive_index:03d}.vpk"
            path.with_name(archive_name).write_bytes(archive_data)
    return path
