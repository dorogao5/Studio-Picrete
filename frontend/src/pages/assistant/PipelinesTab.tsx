import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, FlaskConical, Plus, Save, ScanText, Scale, Trash2, UserCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { apiErrorMessage, pipelinesApi } from "../../lib/api";
import { isKnownAdvisoryModel } from "../../lib/modelPolicy";
import type { Assistant, Pipeline, PipelineStep, Provider } from "../../lib/types";
import { Button, Card, EmptyState, ErrorNote, Field, Input, Select, Spinner } from "../../components/ui";
import { modelOptions } from "./PromptsTab";

type ModelOption = ReturnType<typeof modelOptions>[number];

const STEP_META: Record<string, { label: string; hint: string }> = {
  ocr: { label: "Распознавание работы", hint: "Извлекает текст и формулы из загруженных страниц" },
  grade: { label: "Независимая проверка", hint: "Оценивает решение с нуля по рубрике курса" },
  consensus: { label: "Сверка результатов", hint: "Принимает согласованные оценки и выделяет только существенные расхождения" },
};

const ROLE_LABELS: Record<string, string> = {
  primary: "Основная проверка",
  auditor: "Независимый аудит",
};

function defaultRole(gradeIndex: number) {
  if (gradeIndex === 0) return "primary";
  if (gradeIndex === 1) return "auditor";
  return `reviewer_${gradeIndex + 1}`;
}

function roleLabel(role: string) {
  if (ROLE_LABELS[role]) return ROLE_LABELS[role];
  const reviewer = /^reviewer_(\d+)$/.exec(role);
  if (reviewer) return `Дополнительная проверка ${reviewer[1]}`;
  return "Дополнительная проверка";
}

function roleForStep(step: PipelineStep, gradeIndex: number) {
  const configured = typeof step.config.role === "string" ? step.config.role.trim() : "";
  return configured || defaultRole(gradeIndex);
}

function preferredDecisionModelId(models: ModelOption[], configuredId: string | null) {
  const eligible = models.filter(
    (model) => model.family.toLocaleLowerCase() === "deepseek" && !isKnownAdvisoryModel(model),
  );
  return (
    eligible.find((model) => model.id === configuredId)?.id ??
    eligible.find((model) => model.modelId.toLocaleLowerCase() === "deepseek-v4-pro")?.id ??
    eligible[0]?.id ??
    ""
  );
}

function StepIcon({ type }: { type: string }) {
  if (type === "ocr") return <ScanText className="h-4 w-4" />;
  if (type === "grade") return <UserCheck className="h-4 w-4" />;
  return <Scale className="h-4 w-4" />;
}

export default function PipelinesTab({ assistant, providers }: { assistant: Assistant; providers: Provider[] }) {
  const [pipelines, setPipelines] = useState<Pipeline[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  const reload = async () => {
    try {
      const list = await pipelinesApi.list(assistant.id);
      setPipelines(list);
      setSelected((current) => (current && list.some((pipeline) => pipeline.id === current) ? current : (list[0]?.id ?? null)));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    let cancelled = false;
    setPipelines(null);
    setSelected(null);
    setError("");
    pipelinesApi
      .list(assistant.id)
      .then((list) => {
        if (cancelled) return;
        setPipelines(list);
        setSelected(list[0]?.id ?? null);
      })
      .catch((err) => {
        if (cancelled) return;
        setPipelines([]);
        setError(apiErrorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [assistant.id]);

  const createDefault = async () => {
    setCreating(true);
    setError("");
    try {
      const production = modelOptions(providers, true).filter((model) => !isKnownAdvisoryModel(model));
      const defaultModel = preferredDecisionModelId(production, assistant.default_grader_model_id);
      if (!defaultModel) {
        setError("Подключите production-модель DeepSeek для итоговых решений; облегчённые и Flash-модели здесь не используются.");
        return;
      }
      const created = await pipelinesApi.create(assistant.id, {
        name: "Основной сценарий",
        description: "Распознавание → две независимые проверки → автоматическая сверка",
        steps: [
          { type: "ocr", config: {} },
          { type: "grade", config: { model_entry_id: defaultModel, role: "primary" } },
          { type: "grade", config: { model_entry_id: defaultModel, role: "auditor" } },
          { type: "consensus", config: { disagreement_threshold_pct: 20 } },
        ],
      });
      setSelected(created.id);
      await reload();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setCreating(false);
    }
  };

  if (pipelines === null) return <Spinner />;

  const pipeline = pipelines.find((p) => p.id === selected) ?? null;

  return (
    <div className="space-y-4">
      <ErrorNote message={error} />
      <Card className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold">Сценарии проверки</h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted-foreground">
            Две независимые роли проверяют работу с нуля, а автоматическая сверка пропускает согласованный результат без
            ручного подтверждения. Реального студента и контрольную создавать не нужно.
          </p>
        </div>
        <Link
          to={`/playground?assistant=${assistant.id}`}
          className="inline-flex min-h-10 w-full shrink-0 items-center justify-center gap-2 rounded-md bg-accent px-3.5 py-2 text-sm font-medium text-accent-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 sm:w-auto"
        >
          <FlaskConical className="h-4 w-4" /> Протестировать на работе
        </Link>
      </Card>
      <div className="flex flex-wrap items-center gap-2" aria-label="Сценарии проверки">
        {pipelines.map((p) => (
          <button
            type="button"
            key={p.id}
            onClick={() => setSelected(p.id)}
            aria-pressed={selected === p.id}
            className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 ${
              selected === p.id
                ? "border-accent bg-accent/10 text-accent"
                : "border-border text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            {p.name}
          </button>
        ))}
        <Button variant="secondary" onClick={createDefault} loading={creating}>
          <Plus className="h-4 w-4" /> Новый рекомендуемый сценарий
        </Button>
      </div>

      {pipeline === null ? (
        <EmptyState
          title="Сценариев пока нет"
          hint="Создайте рекомендуемый сценарий: распознавание, основная проверка, независимый аудит и автоматическая сверка."
        />
      ) : (
        <PipelineEditor key={pipeline.id} pipeline={pipeline} assistant={assistant} providers={providers} onChanged={reload} />
      )}
    </div>
  );
}

function PipelineEditor({
  pipeline,
  assistant,
  providers,
  onChanged,
}: {
  pipeline: Pipeline;
  assistant: Assistant;
  providers: Provider[];
  onChanged: () => void;
}) {
  const production = useMemo(
    () => modelOptions(providers, true).filter((model) => !isKnownAdvisoryModel(model)),
    [providers],
  );
  const preferredGraderId = preferredDecisionModelId(production, assistant.default_grader_model_id);
  const [name, setName] = useState(pipeline.name);
  const [steps, setSteps] = useState<PipelineStep[]>(pipeline.steps);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const gradeSteps = steps.filter((step) => step.type === "grade");
  const gradeCount = gradeSteps.length;
  const gradeRoles = gradeSteps.map(roleForStep);
  const availableModelIds = new Set(production.map((model) => model.id));
  const hasUniqueGradeRoles = new Set(gradeRoles).size === gradeRoles.length;
  const hasOcr = steps.some((step) => step.type === "ocr");
  const hasConsensus = steps.some((step) => step.type === "consensus");

  const validDraftOrder = (candidate: PipelineStep[]) => {
    const ocrIndex = candidate.findIndex((step) => step.type === "ocr");
    const consensusIndex = candidate.findIndex((step) => step.type === "consensus");
    return (
      candidate.filter((step) => step.type === "ocr").length <= 1 &&
      (ocrIndex === -1 || ocrIndex === 0) &&
      candidate.filter((step) => step.type === "consensus").length <= 1 &&
      (consensusIndex === -1 || consensusIndex === candidate.length - 1)
    );
  };

  const canMove = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= steps.length) return false;
    const candidate = [...steps];
    [candidate[index], candidate[target]] = [candidate[target], candidate[index]];
    return validDraftOrder(candidate);
  };

  const updateStep = (index: number, patch: Partial<PipelineStep>) => {
    setSavedAt(null);
    setSteps(steps.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  };

  const move = (index: number, delta: number) => {
    const next = [...steps];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setSavedAt(null);
    setSteps(next);
  };

  let draftError = "";
  if (!name.trim()) draftError = "Укажите название сценария";
  else if (gradeCount === 0) draftError = "Добавьте хотя бы одну проверку";
  else if (steps.some((step) => step.type === "grade" && !step.config.model_entry_id)) {
    draftError = "Выберите модель для каждой проверки";
  } else if (
    steps.some(
      (step) =>
        step.type === "grade" &&
        typeof step.config.model_entry_id === "string" &&
        !availableModelIds.has(step.config.model_entry_id),
    )
  ) {
    draftError = "Замените недоступную или облегчённую модель на production-модель для итоговых решений";
  } else if (hasConsensus && !hasUniqueGradeRoles) {
    draftError = "Для сверки назначьте каждой проверке отдельную роль";
  }

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await pipelinesApi.update(assistant.id, pipeline.id, { name, steps });
      setSavedAt(Date.now());
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <Field label="Название сценария">
          <Input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setSavedAt(null);
            }}
            className="w-full sm:w-72"
          />
        </Field>
        <Button
          variant="destructive"
          loading={deleting}
          className="self-start sm:self-auto"
          aria-label={`Удалить сценарий «${pipeline.name}»`}
          onClick={async () => {
            if (confirm(`Удалить сценарий «${pipeline.name}»?`)) {
              setDeleting(true);
              setError("");
              try {
                await pipelinesApi.remove(assistant.id, pipeline.id);
                await onChanged();
              } catch (err) {
                setError(apiErrorMessage(err));
              } finally {
                setDeleting(false);
              }
            }
          }}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      <div className="space-y-2">
        {steps.map((step, index) => {
          const gradeIndex = steps.slice(0, index).filter((candidate) => candidate.type === "grade").length;
          const role = step.type === "grade" ? roleForStep(step, gradeIndex) : "";
          const title = step.type === "grade" ? roleLabel(role) : (STEP_META[step.type]?.label ?? "Этап проверки");
          const usedByOtherGrades = new Set(
            gradeSteps
              .map(roleForStep)
              .filter((_, candidateIndex) => candidateIndex !== gradeIndex),
          );
          const roleOptions = [
            "primary",
            "auditor",
            ...Array.from({ length: Math.max(2, gradeCount) }, (_, offset) => `reviewer_${offset + 3}`),
          ];
          if (role && !roleOptions.includes(role)) roleOptions.push(role);

          return (
            <Card key={step.type === "grade" ? `grade:${role}` : step.type} className="p-4">
              <div className="flex items-start gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent">
                  <StepIcon type={step.type} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {index + 1}. {title}
                  </p>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{STEP_META[step.type]?.hint}</p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    className="rounded p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-30"
                    onClick={() => move(index, -1)}
                    disabled={!canMove(index, -1)}
                    aria-label={`Переместить этап «${title}» выше`}
                  >
                    <ArrowUp className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    className="rounded p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-30"
                    onClick={() => move(index, 1)}
                    disabled={!canMove(index, 1)}
                    aria-label={`Переместить этап «${title}» ниже`}
                  >
                    <ArrowDown className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    className="rounded p-1.5 text-muted-foreground hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-30 disabled:hover:text-muted-foreground"
                    onClick={() => {
                      setSavedAt(null);
                      setSteps(steps.filter((_, stepIndex) => stepIndex !== index));
                    }}
                    disabled={step.type === "grade" && hasConsensus && gradeCount <= 2}
                    title={
                      step.type === "grade" && hasConsensus && gradeCount <= 2
                        ? "Сначала удалите сверку результатов"
                        : `Удалить этап «${title}»`
                    }
                    aria-label={`Удалить этап «${title}»`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>

              {step.type === "grade" && (
                <div className="mt-3 grid gap-3 sm:ml-11 sm:grid-cols-2">
                  <Field label="Роль в проверке">
                    <Select
                      value={role}
                      onChange={(event) => updateStep(index, { config: { ...step.config, role: event.target.value } })}
                      className="w-full"
                    >
                      {roleOptions.map((optionRole) => (
                        <option
                          key={optionRole}
                          value={optionRole}
                          disabled={hasConsensus && optionRole !== role && usedByOtherGrades.has(optionRole)}
                        >
                          {roleLabel(optionRole)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Модель" hint="В разных ролях можно использовать одну модель: запуски остаются независимыми.">
                    <Select
                      value={(step.config.model_entry_id as string) ?? ""}
                      onChange={(event) =>
                        updateStep(index, { config: { ...step.config, model_entry_id: event.target.value } })
                      }
                      className="w-full"
                    >
                      <option value="">— выберите модель —</option>
                      {production.map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.label}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
              )}
              {step.type === "consensus" && (
                <div className="mt-3 sm:ml-11">
                  <Field
                    label="Допустимое расхождение, % от максимального балла"
                    hint="Если оценки расходятся сильнее, работа попадёт преподавателю; согласованные оценки принимаются автоматически."
                  >
                    <Input
                      type="number"
                      min={0}
                      max={100}
                      value={(step.config.disagreement_threshold_pct as number) ?? 20}
                      onChange={(event) =>
                        updateStep(index, {
                          config: { ...step.config, disagreement_threshold_pct: Number(event.target.value) },
                        })
                      }
                      className="w-32"
                    />
                  </Field>
                </div>
              )}
            </Card>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-2" aria-label="Добавить этап сценария">
        <Button
          variant="secondary"
          disabled={hasOcr}
          title={hasOcr ? "Распознавание уже добавлено" : "Добавить распознавание первым этапом"}
          onClick={() => {
            setSavedAt(null);
            setSteps([{ type: "ocr", config: {} }, ...steps]);
          }}
        >
          <Plus className="h-3.5 w-3.5" /> Распознавание
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            const usedRoles = new Set(gradeRoles);
            let nextRole = ["primary", "auditor"].find((candidate) => !usedRoles.has(candidate));
            if (!nextRole) {
              let reviewerNumber = 3;
              while (usedRoles.has(`reviewer_${reviewerNumber}`)) reviewerNumber += 1;
              nextRole = `reviewer_${reviewerNumber}`;
            }
            const grade = {
              type: "grade",
              config: { model_entry_id: preferredGraderId, role: nextRole },
            } as PipelineStep;
            const consensusIndex = steps.findIndex((step) => step.type === "consensus");
            setSavedAt(null);
            setSteps(
              consensusIndex === -1
                ? [...steps, grade]
                : [...steps.slice(0, consensusIndex), grade, ...steps.slice(consensusIndex)],
            );
          }}
        >
          <Plus className="h-3.5 w-3.5" /> Дополнительная проверка
        </Button>
        <Button
          variant="secondary"
          disabled={gradeCount < 2 || hasConsensus || !hasUniqueGradeRoles}
          title={
            hasConsensus
              ? "Сверка результатов уже добавлена"
              : gradeCount < 2
                ? "Для сверки нужны минимум две независимые проверки"
                : !hasUniqueGradeRoles
                  ? "Назначьте проверкам разные роли"
                  : "Автоматически сопоставить баллы по каждому критерию"
          }
          onClick={() => {
            setSavedAt(null);
            setSteps([...steps, { type: "consensus", config: { disagreement_threshold_pct: 20 } }]);
          }}
        >
          <Plus className="h-3.5 w-3.5" /> Сверка результатов
        </Button>
      </div>

      <ErrorNote message={error} />
      {!error && draftError && <ErrorNote message={draftError} />}
      <div className="flex items-center gap-3">
        <Button onClick={save} loading={saving} disabled={Boolean(draftError)}>
          <Save className="h-4 w-4" /> Сохранить сценарий
        </Button>
        {savedAt && (
          <span className="text-xs text-success" role="status">
            Сохранено
          </span>
        )}
      </div>
    </div>
  );
}
