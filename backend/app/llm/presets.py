PROVIDER_PRESETS: list[dict] = [
    {
        "kind": "yandex",
        "title": "Yandex AI Studio",
        "purpose": "production",
        "base_url": "https://llm.api.cloud.yandex.net/v1",
        "auth_note": (
            "API-ключ сервисного аккаунта Yandex Cloud. Идентификатор модели — в формате "
            "gpt://<folder_id>/<модель> — подставьте свой folder_id."
        ),
        "docs_url": "https://yandex.cloud/ru/docs/ai-studio/concepts/openai-compatibility",
        "models": [
            {"model_id": "gpt://FOLDER_ID/yandexgpt/rc", "display_name": "YandexGPT 5.1 Pro", "family": "yandexgpt", "supports_vision": False, "supports_json": False},
            {"model_id": "gpt://FOLDER_ID/yandexgpt/latest", "display_name": "YandexGPT 5 Pro", "family": "yandexgpt", "supports_vision": False, "supports_json": False},
            {"model_id": "gpt://FOLDER_ID/aliceai-llm", "display_name": "Alice AI LLM", "family": "alice", "supports_vision": False, "supports_json": False},
        ],
    },
    {
        "kind": "deepseek",
        "title": "DeepSeek (официальный API)",
        "purpose": "production",
        "base_url": "https://api.deepseek.com/v1",
        "auth_note": "API-ключ с platform.deepseek.com. Thinking-режим у V4 включён по умолчанию.",
        "docs_url": "https://api-docs.deepseek.com",
        "models": [
            {"model_id": "deepseek-v4-pro", "display_name": "DeepSeek V4 Pro", "family": "deepseek", "supports_vision": False, "supports_json": True},
            {"model_id": "deepseek-v4-flash", "display_name": "DeepSeek V4 Flash", "family": "deepseek", "supports_vision": False, "supports_json": True},
        ],
    },
    {
        "kind": "dashscope",
        "title": "Alibaba Model Studio (Qwen)",
        "purpose": "production",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "auth_note": "API-ключ DashScope (international). Для региона Китай замените base URL на dashscope.aliyuncs.com.",
        "docs_url": "https://www.alibabacloud.com/help/en/model-studio/models",
        "models": [
            {"model_id": "qwen3.7-max", "display_name": "Qwen3.7 Max", "family": "qwen", "supports_vision": False, "supports_json": True},
            {"model_id": "qwen3.7-plus", "display_name": "Qwen3.7 Plus (текст+изображения)", "family": "qwen", "supports_vision": True, "supports_json": True},
            {"model_id": "qwen3.6-flash", "display_name": "Qwen3.6 Flash", "family": "qwen", "supports_vision": False, "supports_json": True},
            {"model_id": "qwen3.5-omni-plus", "display_name": "Qwen3.5 Omni Plus (мультимодальная)", "family": "qwen", "supports_vision": True, "supports_json": False},
        ],
    },
    {
        "kind": "openai",
        "title": "OpenAI (только модель-архитектор)",
        "purpose": "architect",
        "base_url": "https://api.openai.com/v1",
        "auth_note": (
            "Используется только внутри платформы для автогенерации системных промптов. "
            "Студентов и проверку обслуживают DeepSeek/Qwen/Яндекс."
        ),
        "docs_url": "https://developers.openai.com/api/docs/models/gpt-5.5",
        "models": [
            {"model_id": "gpt-5.5", "display_name": "GPT-5.5 (архитектор промптов)", "family": "gpt", "supports_vision": True, "supports_json": True},
        ],
    },
    {
        "kind": "custom",
        "title": "Другой OpenAI-совместимый API",
        "purpose": "production",
        "base_url": "",
        "auth_note": "Любой сервис с эндпоинтом /chat/completions (vLLM, Ollama, OpenRouter, прокси).",
        "docs_url": "",
        "models": [],
    },
]
