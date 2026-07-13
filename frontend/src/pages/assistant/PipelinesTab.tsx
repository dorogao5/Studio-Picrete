import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, FlaskConical, Plus, Save, ScanText, Scale, Trash2, UserCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { apiErrorMessage, pipelinesApi } from "../../lib/api";
import type { Assistant, Pipeline, PipelineStep, Provider } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Select, Spinner } from "../../components/ui";
import { modelOptions } from "./PromptsTab";

const STEP_META: Record<string, { label: string; hint: string }> = {
  ocr: { label: "OCR (DataLab)", hint: "Распознаёт фото решения в Markdown" },
  grade: { label: "Проверка LLM", hint: "Модель проверяет решение по активному промпту" },
  consensus: { label: "Консенсус", hint: "Сравнивает оценки проверяющих, помечает расхождения" },
};

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
      const production = modelOptions(providers, true);
      const defaultModel = production.some((model) => model.id === assistant.default_grader_model_id)
        ? assistant.default_grader_model_id!
        : (production[0]?.id ?? "");
      const created = await pipelinesApi.create(assistant.id, {
        name: "Основной пайплайн",
        description: "OCR → проверка выбранной моделью → решение преподавателя",
        steps: [
          { type: "ocr", config: {} },
          { type: "grade", config: { model_entry_id: defaultModel } },
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
          <h2 className="text-sm font-semibold">Проверить ассистента на реальной работе</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Запустите OCR, выбранные модели и консенсус в Playground, не создавая контрольную в Picrete.
          </p>
        </div>
        <Link to={`/playground?assistant=${assistant.id}`} className="shrink-0">
          <Button variant="accent" className="w-full sm:w-auto">
            <FlaskConical className="h-4 w-4" /> Открыть тестовый запуск
          </Button>
        </Link>
      </Card>
      <div className="flex items-center gap-2 flex-wrap">
        {pipelines.map((p) => (
          <button
            key={p.id}
            onClick={() => setSelected(p.id)}
            className={`rounded-full px-3 py-1 text-xs font-medium border ${
              selected === p.id ? "border-accent bg-accent/10 text-accent" : "border-border text-muted-foreground hover:bg-muted"
            }`}
          >
            {p.name}
          </button>
        ))}
        <Button variant="secondary" onClick={createDefault} loading={creating}>
          <Plus className="h-4 w-4" /> Новый пайплайн
        </Button>
      </div>

      {pipeline === null ? (
        <EmptyState
          title="Пайплайнов нет"
          hint="Пайплайн — это конвейер проверки: OCR → одна или несколько проверяющих моделей → консенсус"
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
  const production = useMemo(() => modelOptions(providers, true), [providers]);
  const preferredGraderId = production.some((model) => model.id === assistant.default_grader_model_id)
    ? assistant.default_grader_model_id!
    : (production[0]?.id ?? "");
  const [name, setName] = useState(pipeline.name);
  const [steps, setSteps] = useState<PipelineStep[]>(pipeline.steps);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const gradeCount = steps.filter((step) => step.type === "grade").length;
  const gradeModelIds = steps
    .filter((step) => step.type === "grade")
    .map((step) => String(step.config.model_entry_id ?? ""));
  const hasDistinctGradeModels = gradeModelIds.every(Boolean) && new Set(gradeModelIds).size === gradeModelIds.length;
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
  if (!name.trim()) draftError = "Укажите название пайплайна";
  else if (gradeCount === 0) draftError = "Добавьте хотя бы одну проверяющую модель";
  else if (steps.some((step) => step.type === "grade" && !step.config.model_entry_id)) {
    draftError = "Выберите модель для каждого шага проверки";
  } else if (hasConsensus && !hasDistinctGradeModels) {
    draftError = "Для консенсуса выберите разные модели-проверщики";
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
      <div className="flex items-end gap-2">
        <Field label="Название пайплайна">
          <Input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setSavedAt(null);
            }}
            className="w-72"
          />
        </Field>
        <Button
          variant="destructive"
          loading={deleting}
          aria-label={`Удалить пайплайн «${pipeline.name}»`}
          onClick={async () => {
            if (confirm(`Удалить пайплайн «${pipeline.name}»?`)) {
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
        {steps.map((step, index) => (
          <Card key={index} className="p-4">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent/10 text-accent shrink-0">
                <StepIcon type={step.type} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">
                    {index + 1}. {STEP_META[step.type]?.label ?? step.type}
                  </span>
                  <Badge>{step.type}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">{STEP_META[step.type]?.hint}</p>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  className="p-1.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                  onClick={() => move(index, -1)}
                  disabled={!canMove(index, -1)}
                  aria-label="Переместить шаг выше"
                >
                  <ArrowUp className="h-4 w-4" />
                </button>
                <button
                  className="p-1.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                  onClick={() => move(index, 1)}
                  disabled={!canMove(index, 1)}
                  aria-label="Переместить шаг ниже"
                >
                  <ArrowDown className="h-4 w-4" />
                </button>
                <button
                  className="p-1.5 text-muted-foreground hover:text-destructive disabled:opacity-30 disabled:hover:text-muted-foreground"
                  onClick={() => {
                    setSavedAt(null);
                    setSteps(steps.filter((_, i) => i !== index));
                  }}
                  disabled={step.type === "grade" && hasConsensus && gradeCount <= 2}
                  title={step.type === "grade" && hasConsensus && gradeCount <= 2 ? "Сначала удалите шаг консенсуса" : "Удалить шаг"}
                  aria-label="Удалить шаг"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>

            {step.type === "grade" && (
              <div className="mt-3 ml-11 flex gap-3 items-end flex-wrap">
                <Field label="Модель-проверщик">
                  <Select
                    value={(step.config.model_entry_id as string) ?? ""}
                    onChange={(e) => updateStep(index, { config: { ...step.config, model_entry_id: e.target.value } })}
                    className="w-72"
                  >
                    <option value="">— выберите модель —</option>
                    {production.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.label}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
            )}
            {step.type === "consensus" && (
              <div className="mt-3 ml-11 flex gap-3 items-end">
                <Field label="Порог расхождения, % от макс. балла" hint="Выше порога — работа помечается для преподавателя">
                  <Input
                    type="number"
                    min={0}
                    max={100}
                    value={(step.config.disagreement_threshold_pct as number) ?? 20}
                    onChange={(e) =>
                      updateStep(index, { config: { ...step.config, disagreement_threshold_pct: Number(e.target.value) } })
                    }
                    className="w-28"
                  />
                </Field>
              </div>
            )}
          </Card>
        ))}
      </div>

      <div className="flex gap-2 flex-wrap">
        <Button
          variant="secondary"
          disabled={hasOcr}
          title={hasOcr ? "Шаг OCR уже добавлен" : "Добавить OCR первым шагом"}
          onClick={() => {
            setSavedAt(null);
            setSteps([{ type: "ocr", config: {} }, ...steps]);
          }}
        >
          <Plus className="h-3.5 w-3.5" /> OCR
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            const grade = { type: "grade", config: { model_entry_id: preferredGraderId } } as PipelineStep;
            const consensusIndex = steps.findIndex((step) => step.type === "consensus");
            setSavedAt(null);
            setSteps(consensusIndex === -1 ? [...steps, grade] : [...steps.slice(0, consensusIndex), grade, ...steps.slice(consensusIndex)]);
          }}
        >
          <Plus className="h-3.5 w-3.5" /> Проверка LLM
        </Button>
        <Button
          variant="secondary"
          disabled={gradeCount < 2 || hasConsensus || !hasDistinctGradeModels}
          title={
            hasConsensus
              ? "Шаг консенсуса уже добавлен"
              : gradeCount < 2
                ? "Для консенсуса нужны минимум две проверки LLM"
                : !hasDistinctGradeModels
                  ? "Для консенсуса выберите разные модели-проверщики"
                : "Свести результаты проверяющих моделей"
          }
          onClick={() => {
            setSavedAt(null);
            setSteps([...steps, { type: "consensus", config: { disagreement_threshold_pct: 20 } }]);
          }}
        >
          <Plus className="h-3.5 w-3.5" /> Консенсус
        </Button>
      </div>

      <ErrorNote message={error} />
      {!error && draftError && <ErrorNote message={draftError} />}
      <div className="flex items-center gap-3">
        <Button onClick={save} loading={saving} disabled={Boolean(draftError)}>
          <Save className="h-4 w-4" /> Сохранить пайплайн
        </Button>
        {savedAt && <span className="text-xs text-success">Сохранено</span>}
      </div>
    </div>
  );
}
