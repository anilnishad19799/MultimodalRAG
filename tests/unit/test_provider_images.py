import io

from PIL import Image

from app.providers.openai_compatible import OpenAICompatibleProvider


def test_groq_adapter_upscales_only_the_request_copy_of_tiny_images() -> None:
    source = io.BytesIO()
    Image.new("RGB", (26, 26), color="red").save(source, format="PNG")
    original = source.getvalue()
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model="qwen/qwen3.8-27b",
        endpoint="https://api.groq.com/openai/v1/chat/completions",
        min_image_dimension=32,
    )

    prepared, mime_type = provider._prepare_image(original, "image/png")

    with Image.open(io.BytesIO(prepared)) as image:
        assert image.size == (32, 32)
    assert mime_type == "image/png"
    assert original != prepared
    assert Image.open(io.BytesIO(original)).size == (26, 26)


def test_provider_keeps_regular_images_unchanged() -> None:
    source = io.BytesIO()
    Image.new("RGB", (40, 50), color="blue").save(source, format="PNG")
    original = source.getvalue()
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model="qwen/qwen3.8-27b",
        endpoint="https://api.groq.com/openai/v1/chat/completions",
        min_image_dimension=32,
    )

    prepared, mime_type = provider._prepare_image(original, "image/png")

    assert prepared == original
    assert mime_type == "image/png"
