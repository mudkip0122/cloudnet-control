"""Persist a random installation ID without collecting hardware identifiers."""
import json
from pathlib import Path
import uuid

DEFAULT_ID_FILE = Path(__file__).resolve().parents[1] / "runtime" / "agent-id.json"


def validate_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("PC ID must be a nonempty string of at most 128 characters")
    return value


def resolve_id(explicit=None, path=DEFAULT_ID_FILE):
    if explicit is not None:
        return validate_id(explicit)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = str(uuid.uuid4())
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"pc_id": value}, stream)
        return value
    except FileExistsError:
        try:
            return validate_id(json.loads(path.read_text(encoding="utf-8"))["pc_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Invalid ID file: {path}. Restore it or provide --pc-id; it was not overwritten") from exc
