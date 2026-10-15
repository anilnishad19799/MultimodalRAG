import base64
import io
import json

import httpx
from PIL import Image

from app.core.errors import ProviderError
from app.providers.base import VisionLanguageProvider


class OpenAICompatibleProvider(VisionLanguageProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        endpoint: str,
        timeout: float = 120.0,
        min_image_dimension: int = 1,
    ):
        self.api_key, self.model, self.endpoint, self.timeout = api_key, model, endpoint, timeout
        self.min_image_dimension = min_image_dimension

    def _prepare_image(self, data: bytes, mime: str) -> tuple[bytes, str]:
        """Upscale tiny images for providers with a minimum dimension requirement.

        Stored originals are never modified; this only changes the request bytes.
        Nearest-neighbor preserves the source pixels without inventing detail.
        """
        if self.min_image_dimension <= 1:
            return data, mime
        try:
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width >= self.min_image_dimension and height >= self.min_image_dimension:
                    return data, mime
                if width < 1 or height < 1:
                    return data, mime
                scale = max(self.min_image_dimension / width, self.min_image_dimension / height)
                size = (max(self.min_image_dimension, round(width * scale)),
                        max(self.min_image_dimension, round(height * scale)))
                resized = image.resize(size, Image.Resampling.NEAREST)
                normalized = resized.convert("RGBA" if "A" in resized.getbands() else "RGB")
                output = io.BytesIO()
                normalized.save(output, format="PNG")
                return output.getvalue(), "image/png"
        except (OSError, ValueError):
            # Preserve the original; the provider will return a useful validation error.
            return data, mime

    def _generate(self, prompt: str, images: list[tuple[bytes, str]], json_output: bool = False) -> str:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for data, mime in images:
            data, mime = self._prepare_image(data, mime)
            encoded = base64.b64encode(data).decode("ascii")
            # Let the provider choose image detail: Groq's OpenAI-compatible
            # vision endpoint does not accept every OpenAI-only image option.
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        try:
            request_body = {"model": self.model, "messages": [{"role": "user", "content": content}], "temperature": 0}
            if json_output:
                request_body["response_format"] = {"type": "json_object"}
            response = httpx.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=request_body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            answer = body["choices"][0]["message"]["content"]
            if isinstance(answer, list):
                answer = "".join(part.get("text", "") for part in answer if isinstance(part, dict))
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("empty model response")
            return answer.strip()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000].replace("\n", " ")
            raise ProviderError(
                f"Vision model request failed (HTTP {exc.response.status_code}): {detail}"
            ) from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"Vision model request failed: {exc}") from exc

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
        content: list[dict] = [{"type": "text", "text": prompt}]
        for data, mime in images:
            data, mime = self._prepare_image(data, mime)
            encoded = base64.b64encode(data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        request_body = {
            "model": self.model, "messages": [{"role": "user", "content": content}],
            "temperature": 0, "stream": True, "response_format": {"type": "json_object"},
        }
        try:
            with httpx.stream(
                "POST", self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=request_body, timeout=self.timeout,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    delta = event["choices"][0].get("delta", {}).get("content")
                    if isinstance(delta, str) and delta:
                        yield delta
                    elif isinstance(delta, list):
                        for part in delta:
                            if isinstance(part, dict) and part.get("text"):
                                yield part["text"]
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000].replace("\n", " ")
            raise ProviderError(f"Vision model stream failed (HTTP {exc.response.status_code}): {detail}") from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"Vision model stream failed: {exc}") from exc
