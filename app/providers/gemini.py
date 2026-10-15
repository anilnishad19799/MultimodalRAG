import base64
import json

import httpx

from app.core.errors import ProviderError
from app.providers.base import VisionLanguageProvider


class GeminiProvider(VisionLanguageProvider):
    def __init__(self, api_key: str, model: str, timeout: float = 120.0):
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def _generate(self, prompt: str, images: list[tuple[bytes, str]], json_output: bool = False) -> str:
        parts: list[dict] = [{"text": prompt}]
        for data, mime in images:
            parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        try:
            generation_config = {"temperature": 0}
            if json_output:
                generation_config["responseMimeType"] = "application/json"
            response = httpx.post(
                url,
                headers={"x-goog-api-key": self.api_key},
                json={
                    "contents": [{"parts": parts}],
                    "generationConfig": generation_config,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            answer = "".join(
                part.get("text", "")
                for part in response.json()["candidates"][0]["content"]["parts"]
                if not part.get("thought")
            )
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("empty model response")
            return answer.strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"Gemini request failed: {exc}") from exc

    def summarize_image(self, image: bytes, mime_type: str, source_hint: str) -> str:
        prompt = (
            "Describe the visible evidence in this document image for semantic retrieval. "
            "For charts, state the chart title, cohort/series, axis labels, time periods, units, and each "
            "legible value as explicit label-value pairs; do not omit values in favor of a generic trend summary. "
            "Also transcribe legible table headers, labels, numbers, trends, and relationships. "
            "Separate observations from uncertainty; do not infer unseen facts. "
            f"Source context: {source_hint}"
        )
        return self._generate(prompt, [(image, mime_type)])

    def answer(self, prompt: str, images: list[tuple[bytes, str]]) -> str:
        return self._generate(prompt, images, json_output=True)

    def stream_answer(self, prompt: str, images: list[tuple[bytes, str]]):
        parts: list[dict] = [{"text": prompt}]
        for data, mime in images:
            parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:streamGenerateContent?alt=sse"
        try:
            with httpx.stream(
                "POST", url, headers={"x-goog-api-key": self.api_key},
                json={"contents": [{"parts": parts}], "generationConfig": {
                    "temperature": 0, "responseMimeType": "application/json",
                }}, timeout=self.timeout,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    for part in event.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                        if part.get("text") and not part.get("thought"):
                            yield part["text"]
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000].replace("\n", " ")
            raise ProviderError(f"Gemini stream failed (HTTP {exc.response.status_code}): {detail}") from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"Gemini stream failed: {exc}") from exc
