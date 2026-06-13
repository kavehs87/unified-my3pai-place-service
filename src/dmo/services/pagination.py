import base64
import json
from uuid import UUID


def encode_cursor(entity_id: UUID, sort_key: str | float | int) -> str:
    """Encode entity_id and sort_key into a cursor string."""
    data = json.dumps({"id": str(entity_id), "sort": sort_key})
    return base64.urlsafe_b64encode(data.encode()).decode()


def decode_cursor(cursor: str) -> tuple[UUID, str | float | int]:
    """Decode a cursor string into (entity_id, sort_key)."""
    data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    return UUID(data["id"]), data["sort"]
