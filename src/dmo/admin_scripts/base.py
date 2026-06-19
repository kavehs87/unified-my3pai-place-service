from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.admin.llm_client import LLMClient


@dataclass
class ScriptParameter:
    name: str
    label: str
    type: str = "text"
    default: Any = ""
    options: list[str] | None = None
    required: bool = False
    description: str = ""


@dataclass
class ScriptResult:
    success: bool = True
    message: str = ""
    affected_count: int = 0
    details: list[dict] = field(default_factory=list)


@dataclass
class ScriptMeta:
    name: str
    description: str
    parameters: list[ScriptParameter]
    category: str = "General"


class AdminScript(ABC):
    meta: ScriptMeta

    @abstractmethod
    async def run(
        self,
        params: dict[str, Any],
        db: AsyncSession,
        llm: LLMClient | None = None,
        progress_callback: Any = None,
    ) -> ScriptResult: ...
