import hashlib
from dataclasses import asdict, dataclass

from app.config import Settings, get_settings
from app.models import ModelEntry


DECISION_TIER = "decision"
ADVISORY_TIER = "advisory"


class ModelUsePolicyError(ValueError):
    pass


def _model_ids(value: str) -> frozenset[str]:
    return frozenset(item.strip().casefold() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class ModelUse:
    model_id: str
    tier: str
    decision_capable: bool
    explicitly_configured: bool
    policy_version: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ModelUsePolicy:
    version: str
    decision_model_ids: frozenset[str]
    advisory_model_ids: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings) -> "ModelUsePolicy":
        decision = _model_ids(settings.decision_model_ids)
        advisory = _model_ids(settings.advisory_model_ids) - decision
        fingerprint_source = "\n".join(
            [
                settings.model_use_policy_version.strip(),
                *sorted(f"decision:{model_id}" for model_id in decision),
                *sorted(f"advisory:{model_id}" for model_id in advisory),
            ]
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()[:12]
        return cls(
            version=f"{settings.model_use_policy_version.strip()}:{fingerprint}",
            decision_model_ids=decision,
            advisory_model_ids=advisory,
        )

    def classify(self, model: ModelEntry | str | None) -> ModelUse:
        raw_model_id = getattr(model, "model_id", model) if model is not None else ""
        raw_model_id = str(raw_model_id or "")
        model_id = raw_model_id.strip()
        normalized = model_id.casefold()
        if normalized in self.decision_model_ids:
            return ModelUse(
                model_id=model_id,
                tier=DECISION_TIER,
                decision_capable=True,
                explicitly_configured=True,
                policy_version=self.version,
                reason="Модель разрешена для итоговых решений",
            )
        configured = normalized in self.advisory_model_ids
        return ModelUse(
            model_id=model_id,
            tier=ADVISORY_TIER,
            decision_capable=False,
            explicitly_configured=configured,
            policy_version=self.version,
            reason=(
                "Модель разрешена только для предварительного просмотра"
                if configured
                else "Модель отсутствует в allowlist итоговых решений"
            ),
        )


def current_model_use_policy() -> ModelUsePolicy:
    return ModelUsePolicy.from_settings(get_settings())


def require_decision_model(
    model: ModelEntry | str, *, allow_advisory: bool = False, policy: ModelUsePolicy | None = None
) -> ModelUse:
    use = (policy or current_model_use_policy()).classify(model)
    if use.decision_capable or (allow_advisory and use.explicitly_configured):
        return use
    raise ModelUsePolicyError(f"Модель {use.model_id} не разрешена для итогового ответа: {use.reason}")
