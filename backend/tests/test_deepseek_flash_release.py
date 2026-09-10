import asyncio
from types import SimpleNamespace

from app.api.integration import _build_runtime_policy
from app.config import Settings
from app.llm.presets import PROVIDER_PRESETS
from app.services.model_policy import ModelUsePolicy


def test_release_is_allowlisted_but_expired_preview_is_not():
    policy = ModelUsePolicy.from_settings(Settings(_env_file=None))
    assert policy.classify("deepseek-flash").decision_capable
    assert not policy.classify("deepseek-v4.1-flash-expires-on-0910").decision_capable
    assert not policy.classify("deepseek-v4-flash").decision_capable


def test_release_preset_and_published_tutor_share_actual_api_id():
    preset = next(p for p in PROVIDER_PRESETS if p["kind"] == "deepseek")
    model = next(m for m in preset["models"] if m["model_id"] == "deepseek-flash")
    assert model["supports_vision"] and model["supports_json"]
    assert not any("expires-on" in m["model_id"] for m in preset["models"])

    class DB:
        async def get(self, cls, entry_id):
            assert entry_id == "release-entry"
            return SimpleNamespace(model_id="deepseek-flash", enabled=True)

    runtime = asyncio.run(_build_runtime_policy(DB(), SimpleNamespace(default_grader_model_id="release-entry")))
    assert runtime["tutor_model_id"] == runtime["decision_model_id"] == "deepseek-flash"
    assert runtime["tier"] == "decision"
