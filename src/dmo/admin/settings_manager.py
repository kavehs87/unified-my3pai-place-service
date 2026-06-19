import json

import aiofiles
import structlog

from dmo.config import settings

logger = structlog.get_logger()

_DEFAULT_SETTINGS = {
    "llm_endpoint": "",
    "llm_api_key": "",
    "llm_model": "gpt-4o",
    "llm_max_tokens": 1024,
    "llm_temperature": 0.7,
}


async def load_settings() -> dict:
    try:
        async with aiofiles.open(settings.admin_settings_path) as f:
            content = await f.read()
            data = json.loads(content)
            merged = dict(_DEFAULT_SETTINGS)
            merged.update(data)
            return merged
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_SETTINGS)


async def save_settings(data: dict) -> dict:
    current = await load_settings()
    for k in data:
        if k in current:
            current[k] = data[k]
    async with aiofiles.open(settings.admin_settings_path, "w") as f:
        await f.write(json.dumps(current, indent=2))
    return current
