from app.core.config import Settings
from app.providers.gemini import GeminiProvider
from app.providers.openai_compatible import OpenAICompatibleProvider


def create_provider(settings: Settings):
    key = settings.selected_api_key
    if settings.model_provider == "gemini":
        return GeminiProvider(api_key=key, model=settings.selected_model)
    endpoint = "https://api.groq.com/openai/v1/chat/completions" if settings.model_provider == "groq" else "https://api.openai.com/v1/chat/completions"
    min_image_dimension = 32 if settings.model_provider == "groq" else 1
    return OpenAICompatibleProvider(
        api_key=key,
        model=settings.selected_model,
        endpoint=endpoint,
        min_image_dimension=min_image_dimension,
    )
