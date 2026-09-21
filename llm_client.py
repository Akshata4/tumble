import os

from openai import OpenAI

PROVIDER_DEFAULTS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat-v3.1",
    },
    "wandb": {
        "base_url": "https://api.inference.wandb.ai/v1",
        "model": "deepseek-ai/DeepSeek-V3.1",
    },
}


def get_client_and_model() -> tuple[OpenAI, str]:
    """Tumble defaults to W&B Inference, since it's built specifically to
    run its research reasoning on W&B credits. OpenRouter stays supported
    as a fallback -- both speak the same OpenAI chat-completions schema, so
    the client code itself never branches on which one is active."""
    provider = os.environ.get("LLM_PROVIDER", "wandb").lower()
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"Unknown LLM_PROVIDER={provider!r}; use 'openrouter' or 'wandb'")

    defaults = PROVIDER_DEFAULTS[provider]
    model = os.environ.get("LLM_MODEL") or defaults["model"]

    if provider == "wandb":
        client = OpenAI(
            base_url=defaults["base_url"],
            api_key=os.environ["WANDB_API_KEY"],
            project=os.environ["WANDB_PROJECT"],
        )
    else:  # openrouter
        client = OpenAI(
            base_url=defaults["base_url"],
            api_key=os.environ["OPENROUTER_API_KEY"],
            default_headers={
                "HTTP-Referer": "https://github.com/local/harness-from-scratch",
                "X-Title": "harness-from-scratch",
            },
        )

    return client, model
