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

  const reload = async () => {
    try {
      const list = await pipelinesApi.list(assistant.id);
      setPipelines(list);
      if (list.length > 0 && !selected) setSelected(list[0].id);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    void reload();
  }, [assistant.id]);

  const createDefault = async () => {
    const production = modelOptions(providers, true);
    const defaultModel = production.some((model) => model.id === assistant.default_grader_model_id)
      ? assistant.default_grader_model_id!
      : (production[0]?.id ?? "");
    await pipelinesApi.create(assistant.id, {
      name: "Основной пайплайн",
      description: "OCR → проверка DeepSeek Pro → решение преподавателя",
      steps: [
        { type: "ocr", config: {} },
        { type: "grade", config: { model_entry_id: defaultModel } },
      ],
    });
    await reload();
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
        <Button variant="secondary" onClick={createDefault}>
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
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const updateStep = (index: number, patch: Partial<PipelineStep>) => {
    setSteps(steps.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  };

  const move = (index: number, delta: number) => {
    const next = [...steps];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setSteps(next);
  };

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
          <Input value={name} onChange={(e) => setName(e.target.value)} className="w-72" />
        </Field>
        <Button
          variant="destructive"
          onClick={async () => {
            if (confirm(`Удалить пайплайн «${pipeline.name}»?`)) {
              await pipelinesApi.remove(assistant.id, pipeline.id);
              onChanged();
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
                <button className="p-1.5 text-muted-foreground hover:text-foreground disabled:opacity-30" onClick={() => move(index, -1)} disabled={index === 0}>
                  <ArrowUp className="h-4 w-4" />
                </button>
                <button
                  className="p-1.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                  onClick={() => move(index, 1)}
                  disabled={index === steps.length - 1}
                >
                  <ArrowDown className="h-4 w-4" />
                </button>
                <button
                  className="p-1.5 text-muted-foreground hover:text-destructive"
                  onClick={() => setSteps(steps.filter((_, i) => i !== index))}
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
        <Button variant="secondary" onClick={() => setSteps([{ type: "ocr", config: {} }, ...steps])}>
          <Plus className="h-3.5 w-3.5" /> OCR
        </Button>
        <Button
          variant="secondary"
          onClick={() => setSteps([...steps, { type: "grade", config: { model_entry_id: preferredGraderId } }])}
        >
          <Plus className="h-3.5 w-3.5" /> Проверка LLM
        </Button>
        <Button
          variant="secondary"
          title="Для консенсуса нужны минимум две проверки LLM"
          onClick={() => setSteps([...steps, { type: "consensus", config: { disagreement_threshold_pct: 20 } }])}
        >
          <Plus className="h-3.5 w-3.5" /> Консенсус
        </Button>
      </div>

      <ErrorNote message={error} />
      <div className="flex items-center gap-3">
        <Button onClick={save} loading={saving}>
          <Save className="h-4 w-4" /> Сохранить пайплайн
        </Button>
        {savedAt && <span className="text-xs text-success">Сохранено</span>}
      </div>
    </div>
  );
}
