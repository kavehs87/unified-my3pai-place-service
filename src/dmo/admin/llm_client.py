import json

import structlog

logger = structlog.get_logger()


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    @classmethod
    def from_settings(cls, settings: dict) -> "LLMClient | None":
        endpoint = settings.get("llm_endpoint", "")
        api_key = settings.get("llm_api_key", "")
        if not endpoint or not api_key:
            return None
        return cls(
            endpoint=endpoint,
            api_key=api_key,
            model=settings.get("llm_model", "gpt-4o"),
            max_tokens=settings.get("llm_max_tokens", 1024),
            temperature=settings.get("llm_temperature", 0.7),
        )

    async def chat(self, messages: list[dict], **overrides) -> str:
        import httpx

        url = f"{self.endpoint}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": overrides.get("model", self.model),
            "messages": messages,
            "max_tokens": overrides.get("max_tokens", self.max_tokens),
            "temperature": overrides.get("temperature", self.temperature),
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content", "").strip()
                if not content.startswith("{") and msg.get("reasoning_content"):
                    content = msg.get("reasoning_content", "").strip()
                if not content:
                    raise LLMError("Empty response from LLM")
                return content
        except httpx.HTTPStatusError as e:
            raise LLMError(f"LLM API returned {e.response.status_code}: {e.response.text[:200]}")
        except httpx.RequestError as e:
            raise LLMError(f"LLM request failed: {e}")
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError(f"LLM response parse error: {e}")

    async def test_connection(self) -> str:
        try:
            result = await self.chat(
                [{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=10,
                temperature=0,
            )
            return result.strip()
        except LLMError as e:
            return f"ERROR: {e}"
