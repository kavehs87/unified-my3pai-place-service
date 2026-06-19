import importlib
import inspect
import pkgutil
from typing import Any

import structlog

from dmo.admin_scripts.base import AdminScript

logger = structlog.get_logger()

_registry: dict[str, type[AdminScript]] | None = None


def discover_scripts() -> dict[str, type[AdminScript]]:
    global _registry
    if _registry is not None:
        return _registry

    _registry = {}
    import dmo.admin_scripts as pkg

    for _importer, modname, _ispkg in pkgutil.iter_modules(pkg.__path__):
        if modname in ("base", "registry", "__init__"):
            continue
        try:
            mod = importlib.import_module(f"dmo.admin_scripts.{modname}")
            for _name, obj in inspect.getmembers(mod, inspect.isclass):
                if issubclass(obj, AdminScript) and obj is not AdminScript:
                    instance = obj()
                    meta = instance.meta
                    _registry[meta.name] = obj
                    logger.info("discovered_admin_script", name=meta.name)
        except Exception as e:
            logger.warning("failed_to_load_script", module=modname, error=str(e))

    return _registry


def get_script(name: str) -> AdminScript | None:
    registry = discover_scripts()
    cls = registry.get(name)
    if cls is None:
        return None
    instance = cls()
    return instance


def list_scripts() -> list[dict[str, Any]]:
    registry = discover_scripts()
    result = []
    for _name, cls in registry.items():
        instance = cls()
        meta = instance.meta
        result.append(
            {
                "name": meta.name,
                "description": meta.description,
                "category": meta.category,
                "parameters": [
                    {
                        "name": p.name,
                        "label": p.label,
                        "type": p.type,
                        "default": p.default,
                        "options": p.options,
                        "required": p.required,
                        "description": p.description,
                    }
                    for p in meta.parameters
                ],
            }
        )
    return sorted(result, key=lambda x: (x["category"], x["name"]))
