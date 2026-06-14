import base64
import json
from uuid import UUID

from dmo.exceptions import AppError


def encode_cursor(primary_id: UUID | int, sort_key: str | float | int) -> str:
    """Encode primary_id and sort_key into a cursor string."""
    data = json.dumps({"id": str(primary_id), "sort": sort_key})
    return base64.urlsafe_b64encode(data.encode()).decode()


def decode_cursor(cursor: str) -> tuple[UUID | int, str | float | int]:
    """Decode a cursor string into (primary_id, sort_key)."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        raw_id = data["id"]
        sort_key = data["sort"]
    except (ValueError, KeyError, TypeError):
        raise AppError("Invalid cursor format", "InvalidCursor", 400)

    try:
        uuid_val = UUID(raw_id)
        if str(uuid_val) == raw_id:
            return uuid_val, sort_key
    except (ValueError, AttributeError):
        pass
    try:
        return int(raw_id), sort_key
    except (ValueError, AttributeError):
        raise AppError("Invalid cursor format", "InvalidCursor", 400)
