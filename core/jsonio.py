import json
import os
import uuid
from pathlib import Path


def write_json_atomic(path: Path, data: dict) -> None:
    """Пишет JSON через временный файл, чтобы читатель не увидел обрезанные данные."""
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
