import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ChevronDown,
  ChevronRight,
  Crown,
  History,
  Lightbulb,
  Loader2,
  Plus,
  Play,
  RotateCcw,
  ScanText,
  Send,
  Star,
  Trash2,
  Upload,
} from "lucide-react";
import {
  apiErrorMessage,
  assistantsApi,
  pipelinesApi,
  playgroundApi,
  promptsApi,
  providersApi,
  tasksApi,
  tutorApi,
} from "../lib/api";
import { useApp } from "../lib/context";
import type {
  Assistant,
  GeneratedTask,
  Pipeline,
  PipelineRun,
  PlaygroundResult,
  PlaygroundRun,
  PromptVersion,
  Provider,
  TutorMessage,
  TutorRun,
} from "../lib/types";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Select, Spinner, Tabs, Textarea } from "../components/ui";
import MathText from "../components/MathText";
import { modelOptions } from "./assistant/PromptsTab";

type RubricCriterion = GeneratedTask["rubric"][number];

const SCORE_TOLERANCE = 1e-6;

function rubricFromAssistant(assistant: Assistant): RubricCriterion[] {
  return assistant.criteria.map((criterion) => ({
    criterion_name: criterion.name,
    max_score: criterion.max_score,
    description: criterion.description,
  }));
}

function rubricTotal(rubric: RubricCriterion[]): number {
  return rubric.reduce((total, criterion) => total + (Number.isFinite(criterion.max_score) ? criterion.max_score : 0), 0);
}

function defaultMaxScore(rubric: RubricCriterion[]): number {
  const total = rubricTotal(rubric);
  return total > 0 ? total : 10;
}

function formatScore(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
}

function validateRubric(rubric: RubricCriterion[], maxScore: number): string {
  if (rubric.length === 0) return "Добавьте хотя бы один критерий оценивания.";

  const seen = new Set<string>();
  for (let index = 0; index < rubric.length; index += 1) {
    const criterion = rubric[index];
    const name = criterion.criterion_name.trim();
    if (!name) return `Укажите название критерия ${index + 1}.`;
    if (seen.has(name)) return `Критерий «${name}» добавлен повторно.`;
    if (!Number.isFinite(criterion.max_score) || criterion.max_score <= 0) {
      return `Баллы критерия «${name}» должны быть больше нуля.`;
    }
    seen.add(name);
  }

  if (!Number.isFinite(maxScore) || maxScore <= 0) return "Максимальный балл задачи должен быть больше нуля.";

  const total = rubricTotal(rubric);
  if (Math.abs(total - maxScore) > SCORE_TOLERANCE) {
    return `Сумма критериев — ${formatScore(total)}, максимум задачи — ${formatScore(maxScore)}. Исправьте значения перед запуском.`;
  }
  return "";
}

function normalizedRubric(rubric: RubricCriterion[]): RubricCriterion[] {
  return rubric.map((criterion) => ({
    ...criterion,
    criterion_name: criterion.criterion_name.trim(),
    description: criterion.description?.trim() || "",
  }));
}

export default function Playground() {
  const [params, setParams] = useSearchParams();
  const { selectedId, setSelectedId } = useApp();
  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [mode, setMode] = useState("compare");

  const assistantId = params.get("assistant") ?? "";
  const assistant = assistants.find((a) => a.id === assistantId) ?? null;

  useEffect(() => {
    assistantsApi.list().then((list) => {
      setAssistants(list);
      if (!assistantId) {
        const initial = (selectedId && list.some((a) => a.id === selectedId) ? selectedId : list[0]?.id) ?? "";
        if (initial) setParams({ assistant: initial }, { replace: true });
      }
    });
    providersApi.list().then(setProviders).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="max-w-6xl space-y-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold">Playground</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            E2E-тестирование: задача → фото решения → OCR → проверка моделями → ваша экспертная оценка
          </p>
        </div>
        <Select
          value={assistantId}
          onChange={(e) => {
            setParams({ assistant: e.target.value });
            setSelectedId(e.target.value);
          }}
          className="w-72"
        >
          {assistants.length === 0 && <option value="">— создайте дисциплину —</option>}
          {assistants.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </Select>
      </div>

      <Tabs
        tabs={[
          { key: "compare", label: "Сравнение моделей" },
          { key: "pipeline", label: "Пайплайн E2E" },
          { key: "tutor", label: "Разбор со студентом" },
          { key: "history", label: "История" },
        ]}
        active={mode}
        onChange={setMode}
      />

      {assistant === null ? (
        <EmptyState title="Выберите ассистента" />
      ) : mode === "compare" ? (
        <CompareMode assistant={assistant} providers={providers} />
      ) : mode === "pipeline" ? (
        <PipelineMode assistant={assistant} />
      ) : mode === "tutor" ? (
        <TutorMode assistant={assistant} providers={providers} />
      ) : (
        <HistoryMode assistant={assistant} />
      )}
    </div>
  );
}

function useSolutionInput() {
  const [ocrText, setOcrText] = useState("");
  const [imageIds, setImageIds] = useState<string[]>([]);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrError, setOcrError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setOcrLoading(true);
    setOcrError("");
    try {
      const result = await playgroundApi.ocr(Array.from(files));
      setOcrText((prev) => (prev ? `${prev}\n\n---\n\n${result.ocr_text}` : result.ocr_text));
      setImageIds((prev) => [...prev, ...result.image_ids]);
    } catch (err) {
      setOcrError(apiErrorMessage(err));
    } finally {
      setOcrLoading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const node = (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,application/pdf"
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <Button variant="secondary" onClick={() => fileRef.current?.click()} loading={ocrLoading}>
          <Upload className="h-4 w-4" /> Фото решения → OCR
        </Button>
        {imageIds.length > 0 && <Badge tone="info">{imageIds.length} стр.</Badge>}
        <span className="text-xs text-muted-foreground">DataLab распознает рукопись; результат можно править ниже</span>
      </div>
      <ErrorNote message={ocrError} />
      <Textarea
        rows={8}
        value={ocrText}
        onChange={(e) => setOcrText(e.target.value)}
        placeholder="OCR-текст решения студента появится здесь — или вставьте текст решения вручную"
      />
    </div>
  );

  return { ocrText, imageIds, node };
}

function ManualRubricEditor({
  rubric,
  setRubric,
  maxScore,
  setMaxScore,
  error,
}: {
  rubric: RubricCriterion[];
  setRubric: (rubric: RubricCriterion[]) => void;
  maxScore: number;
  setMaxScore: (score: number) => void;
  error: string;
}) {
  const updateCriterion = (index: number, patch: Partial<RubricCriterion>) => {
    setRubric(rubric.map((criterion, current) => (current === index ? { ...criterion, ...patch } : criterion)));
  };

  return (
    <div className="space-y-3 rounded-md border border-border bg-muted/15 p-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm font-medium">Рубрика проверки</p>
          <p className="mt-0.5 text-xs text-muted-foreground">Критерии попадут в каждую проверку без изменений.</p>
        </div>
        <div className="flex items-end gap-2">
          <span className="pb-2 text-xs text-muted-foreground">Сумма: {formatScore(rubricTotal(rubric))}</span>
          <label className="block w-24 space-y-1">
            <span className="text-xs font-medium">Максимум</span>
            <Input
              type="number"
              min={0}
              step={0.5}
              value={Number.isFinite(maxScore) ? maxScore : ""}
              onChange={(event) => setMaxScore(event.target.value === "" ? Number.NaN : Number(event.target.value))}
              aria-label="Максимальный балл задачи"
            />
          </label>
        </div>
      </div>

      {rubric.length === 0 ? (
        <div className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
          Без критериев автоматическая оценка не запускается.
        </div>
      ) : (
        <div className="space-y-2">
          {rubric.map((criterion, index) => (
            <div
              key={index}
              className="grid gap-2 rounded-md border border-border bg-card p-2 sm:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)_5.5rem_2.25rem]"
            >
              <Input
                value={criterion.criterion_name}
                onChange={(event) => updateCriterion(index, { criterion_name: event.target.value })}
                placeholder="Критерий"
                aria-label={`Название критерия ${index + 1}`}
              />
              <Input
                value={criterion.description ?? ""}
                onChange={(event) => updateCriterion(index, { description: event.target.value })}
                placeholder="Что проверяем"
                aria-label={`Описание критерия ${index + 1}`}
              />
              <Input
                type="number"
                min={0}
                step={0.5}
                value={Number.isFinite(criterion.max_score) ? criterion.max_score : ""}
                onChange={(event) =>
                  updateCriterion(index, {
                    max_score: event.target.value === "" ? Number.NaN : Number(event.target.value),
                  })
                }
                aria-label={`Максимальный балл критерия ${index + 1}`}
              />
              <Button
                type="button"
                variant="ghost"
                className="h-10 !px-2 text-muted-foreground hover:text-destructive"
                aria-label={`Удалить критерий ${index + 1}`}
                onClick={() => setRubric(rubric.filter((_, current) => current !== index))}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      )}

      <Button
        type="button"
        variant="secondary"
        className="text-xs"
        onClick={() => setRubric([...rubric, { criterion_name: "", max_score: 1, description: "" }])}
      >
        <Plus className="h-3.5 w-3.5" /> Добавить критерий
      </Button>
      {error && (
        <p role="alert" className="text-xs font-medium text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

function TaskPicker({
  assistant,
  taskId,
  setTaskId,
  taskText,
  setTaskText,
  referenceSolution,
  setReferenceSolution,
  rubric,
  setRubric,
  maxScore,
  setMaxScore,
  rubricError,
  onSelectedTaskChange,
}: {
  assistant: Assistant;
  taskId: string;
  setTaskId: (v: string) => void;
  taskText: string;
  setTaskText: (v: string) => void;
  referenceSolution: string;
  setReferenceSolution: (v: string) => void;
  rubric: RubricCriterion[];
  setRubric: (rubric: RubricCriterion[]) => void;
  maxScore: number;
  setMaxScore: (score: number) => void;
  rubricError: string;
  onSelectedTaskChange: (task: GeneratedTask | null) => void;
}) {
  const [tasks, setTasks] = useState<GeneratedTask[]>([]);
  const [loadingTasks, setLoadingTasks] = useState(true);
  const [tasksError, setTasksError] = useState("");
  const selectedTask = useMemo(() => tasks.find((task) => task.id === taskId) ?? null, [tasks, taskId]);

  useEffect(() => {
    let cancelled = false;
    setTasks([]);
    setLoadingTasks(true);
    setTasksError("");
    tasksApi
      .list(assistant.id)
      .then((list) => {
        if (!cancelled) setTasks(list);
      })
      .catch((err) => {
        if (!cancelled) setTasksError(apiErrorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingTasks(false);
      });
    return () => {
      cancelled = true;
    };
  }, [assistant.id]);

  return (
    <div className="space-y-3">
      <Field label="Задача из банка">
        <Select
          value={taskId}
          onChange={(event) => {
            const nextId = event.target.value;
            setTaskId(nextId);
            onSelectedTaskChange(tasks.find((task) => task.id === nextId) ?? null);
          }}
        >
          <option value="">{loadingTasks ? "— загружаем банк задач —" : "— ввести условие вручную —"}</option>
          {tasks.map((t) => (
            <option key={t.id} value={t.id}>
              {(t.approved ? "✓ " : "") + t.statement.slice(0, 90)}
            </option>
          ))}
        </Select>
      </Field>
      <ErrorNote message={tasksError} />
      {!taskId && (
        <>
          <Field label="Условие задачи">
            <Textarea rows={4} value={taskText} onChange={(e) => setTaskText(e.target.value)} />
          </Field>
          <Field label="Эталонное решение (желательно)">
            <Textarea rows={4} value={referenceSolution} onChange={(e) => setReferenceSolution(e.target.value)} />
          </Field>
          <ManualRubricEditor
            rubric={rubric}
            setRubric={setRubric}
            maxScore={maxScore}
            setMaxScore={setMaxScore}
            error={rubricError}
          />
        </>
      )}
      {taskId && selectedTask && (
        <div className="space-y-2 rounded-md border border-border bg-muted/20 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={selectedTask.approved ? "success" : "default"}>
              {selectedTask.approved ? "Одобрена" : "Не одобрена"}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {selectedTask.topic || "Без темы"} · максимум {selectedTask.max_score} баллов
            </span>
          </div>
          <MathText className="text-sm">{selectedTask.statement}</MathText>
          {(selectedTask.reference_solution || selectedTask.rubric.length > 0) && (
            <details className="text-xs">
              <summary className="cursor-pointer font-medium text-accent">Эталон и критерии</summary>
              {selectedTask.reference_solution && (
                <div className="mt-2 rounded border border-border bg-card p-2.5">
                  <MathText>{selectedTask.reference_solution}</MathText>
                </div>
              )}
              {selectedTask.rubric.length > 0 && (
                <ul className="mt-2 space-y-1 text-muted-foreground">
                  {selectedTask.rubric.map((criterion, index) => (
                    <li key={`${criterion.criterion_name}-${index}`}>
                      <span className="font-medium text-foreground">{criterion.criterion_name}</span>
                      {` — ${criterion.max_score} балл.`}
                    </li>
                  ))}
                </ul>
              )}
            </details>
          )}
        </div>
      )}
      {taskId && rubricError && (
        <p role="alert" className="text-xs font-medium text-destructive">
          {rubricError}
        </p>
      )}
    </div>
  );
}

function CompareMode({ assistant, providers }: { assistant: Assistant; providers: Provider[] }) {
  const production = useMemo(() => modelOptions(providers, true), [providers]);
  const [prompts, setPrompts] = useState<PromptVersion[]>([]);
  const [promptVersionId, setPromptVersionId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [taskText, setTaskText] = useState("");
  const [referenceSolution, setReferenceSolution] = useState("");
  const [manualRubric, setManualRubric] = useState<RubricCriterion[]>(() => rubricFromAssistant(assistant));
  const [manualMaxScore, setManualMaxScore] = useState(() => defaultMaxScore(rubricFromAssistant(assistant)));
  const [selectedTask, setSelectedTask] = useState<GeneratedTask | null>(null);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [run, setRun] = useState<PlaygroundRun | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const solution = useSolutionInput();
  const activeRubric = taskId ? selectedTask?.rubric ?? [] : manualRubric;
  const activeMaxScore = taskId ? selectedTask?.max_score ?? 0 : manualMaxScore;
  const rubricError = validateRubric(activeRubric, activeMaxScore);

  useEffect(() => {
    const nextRubric = rubricFromAssistant(assistant);
    setPromptVersionId("");
    setTaskId("");
    setTaskText("");
    setReferenceSolution("");
    setManualRubric(nextRubric);
    setManualMaxScore(defaultMaxScore(nextRubric));
    setSelectedTask(null);
    setRun(null);
    setError("");
    promptsApi.list(assistant.id).then((list) => {
      setPrompts(list.filter((p) => p.role === "grader"));
      const active = list.find((p) => p.role === "grader" && p.status === "active");
      if (active) setPromptVersionId(active.id);
    });
  }, [assistant.id]);

  const toggleModel = (id: string) => {
    setSelectedModels((prev) => (prev.includes(id) ? prev.filter((m) => m !== id) : prev.length < 6 ? [...prev, id] : prev));
  };

  const execute = async () => {
    if (rubricError) return;
    setRunning(true);
    setError("");
    setRun(null);
    try {
      const result = await playgroundApi.compare({
        run_id: crypto.randomUUID().replaceAll("-", ""),
        assistant_id: assistant.id,
        prompt_version_id: promptVersionId || null,
        task_id: taskId || null,
        task_text: taskText,
        reference_solution: referenceSolution,
        rubric: normalizedRubric(activeRubric),
        max_score: activeMaxScore,
        ocr_text: solution.ocrText,
        image_ids: solution.imageIds,
        model_entry_ids: selectedModels,
      });
      setRun(result);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="p-5 space-y-4">
          <h2 className="text-sm font-semibold">1. Задача</h2>
          <TaskPicker
            assistant={assistant}
            taskId={taskId}
            setTaskId={setTaskId}
            taskText={taskText}
            setTaskText={setTaskText}
            referenceSolution={referenceSolution}
            setReferenceSolution={setReferenceSolution}
            rubric={manualRubric}
            setRubric={setManualRubric}
            maxScore={manualMaxScore}
            setMaxScore={setManualMaxScore}
            rubricError={rubricError}
            onSelectedTaskChange={setSelectedTask}
          />
          <Field label="Версия промпта проверки">
            <Select value={promptVersionId} onChange={(e) => setPromptVersionId(e.target.value)}>
              {prompts.length === 0 && <option value="">— нет промптов (вкладка «Промпты») —</option>}
              {prompts.map((p) => (
                <option key={p.id} value={p.id}>
                  v{p.version} · {p.target_family} {p.status === "active" ? "· активен" : ""}
                </option>
              ))}
            </Select>
          </Field>
        </Card>

        <Card className="p-5 space-y-4">
          <h2 className="text-sm font-semibold">2. Решение студента</h2>
          {solution.node}
        </Card>
      </div>

      <Card className="p-5 space-y-3">
        <h2 className="text-sm font-semibold">3. Модели для сравнения (до 6)</h2>
        <div className="flex flex-wrap gap-2">
          {production.length === 0 && (
            <p className="text-xs text-muted-foreground">Подключите production-провайдера на странице «Провайдеры»</p>
          )}
          {production.map((m) => (
            <button
              key={m.id}
              onClick={() => toggleModel(m.id)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium border transition-colors ${
                selectedModels.includes(m.id)
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border text-muted-foreground hover:bg-muted"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <ErrorNote message={error} />
        <Button
          onClick={execute}
          loading={running}
          disabled={Boolean(rubricError) || selectedModels.length === 0 || !solution.ocrText.trim() || (!taskId && !taskText.trim())}
        >
          <Play className="h-4 w-4" /> Запустить проверку {selectedModels.length > 0 && `(${selectedModels.length})`}
        </Button>
        {running && (
          <p className="text-xs text-muted-foreground">
            Модели проверяют решение параллельно — обычно 20–90 секунд...
          </p>
        )}
      </Card>

      {run && <ResultsGrid run={run} />}
    </div>
  );
}

function ResultsGrid({ run }: { run: PlaygroundRun }) {
  const [results, setResults] = useState<PlaygroundResult[]>(run.results);

  const sendFeedback = async (resultId: string, body: { rating?: number; is_winner?: boolean; comment?: string }) => {
    const updated = await playgroundApi.feedback(resultId, body);
    setResults((prev) =>
      prev.map((r) => {
        if (r.id === updated.id) return updated;
        if (body.is_winner && updated.is_winner) return { ...r, is_winner: false };
        return r;
      }),
    );
  };

  return (
    <div>
      <h2 className="text-sm font-semibold mb-2">Результаты — отметьте, какая модель проверила лучше</h2>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {results.map((result) => (
          <ResultCard key={result.id} result={result} assistantId={run.assistant_id} onFeedback={sendFeedback} />
        ))}
      </div>
    </div>
  );
}

function ResultCard({
  result,
  assistantId,
  onFeedback,
}: {
  result: PlaygroundResult;
  assistantId: string;
  onFeedback: (id: string, body: { rating?: number; is_winner?: boolean; comment?: string }) => Promise<void>;
}) {
  const [comment, setComment] = useState(result.feedback_comment);
  const [nuanceOpen, setNuanceOpen] = useState(false);
  const output = result.output;

  const suggestedNuance = (): string => {
    const errs = (output?.detailed_analysis as { errors_found?: unknown } | undefined)?.errors_found;
    if (Array.isArray(errs) && errs.length) return errs.map(String).join("; ");
    return output?.feedback ?? "";
  };

  return (
    <Card className={`p-4 space-y-3 ${result.is_winner ? "ring-2 ring-accent" : ""}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold truncate">{result.provider_name}</p>
          <p className="text-xs text-muted-foreground font-mono truncate">{result.model_id}</p>
        </div>
        {result.status === "completed" && output ? (
          <div className="text-right shrink-0">
            <p className="text-lg font-bold">
              {output.total_score ?? "?"}
              <span className="text-xs font-normal text-muted-foreground"> / {output.max_score ?? "?"}</span>
            </p>
            {typeof output.confidence === "number" && (
              <p className="text-xs text-muted-foreground">уверенность {(output.confidence * 100).toFixed(0)}%</p>
            )}
          </div>
        ) : (
          <Badge tone="destructive">ошибка</Badge>
        )}
      </div>

      {result.error && <p className="text-xs text-destructive whitespace-pre-wrap">{result.error}</p>}

      {output && (
        <>
          {output.needs_teacher_review && <Badge tone="warning">требует внимания преподавателя</Badge>}
          {output.criteria_scores && output.criteria_scores.length > 0 && (
            <div className="space-y-1">
              {output.criteria_scores.map((c, i) => (
                <div key={i} className="text-xs">
                  <div className="flex justify-between gap-2">
                    <span className="font-medium">{c.criterion_name}</span>
                    <span className="shrink-0">
                      {c.score}/{c.max_score}
                    </span>
                  </div>
                  {c.comment && <p className="text-muted-foreground">{c.comment}</p>}
                </div>
              ))}
            </div>
          )}
          {output.feedback && (
            <details className="text-xs">
              <summary className="cursor-pointer font-medium text-muted-foreground">Фидбек студенту</summary>
              <MathText className="mt-1">{output.feedback}</MathText>
            </details>
          )}
        </>
      )}

      <p className="text-xs text-muted-foreground">
        {(result.duration_ms / 1000).toFixed(1)} с{result.tokens_total ? ` · ${result.tokens_total} токенов` : ""}
      </p>

      <div className="border-t border-border pt-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex gap-0.5">
            {[1, 2, 3, 4, 5].map((star) => (
              <button key={star} onClick={() => onFeedback(result.id, { rating: star })}>
                <Star
                  className={`h-4 w-4 ${
                    result.rating && star <= result.rating ? "fill-warning text-warning" : "text-muted-foreground"
                  }`}
                />
              </button>
            ))}
          </div>
          <Button
            variant={result.is_winner ? "accent" : "secondary"}
            onClick={() => onFeedback(result.id, { is_winner: !result.is_winner })}
            className="!px-2.5 !py-1 text-xs"
          >
            <Crown className="h-3.5 w-3.5" /> {result.is_winner ? "Лучшая" : "Отметить лучшей"}
          </Button>
        </div>
        <div className="flex gap-2">
          <Input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Почему? Это станет датасетом экспертных решений"
            className="text-xs"
            onBlur={() => {
              if (comment !== result.feedback_comment) void onFeedback(result.id, { comment });
            }}
          />
        </div>
        <Button variant="ghost" className="w-full !justify-start text-xs" onClick={() => setNuanceOpen(true)}>
          <Lightbulb className="h-3.5 w-3.5" /> В нюансы дисциплины
        </Button>
      </div>

      <NuanceModal
        open={nuanceOpen}
        onClose={() => setNuanceOpen(false)}
        assistantId={assistantId}
        suggestion={suggestedNuance()}
      />
    </Card>
  );
}

function NuanceModal({
  open,
  onClose,
  assistantId,
  suggestion,
}: {
  open: boolean;
  onClose: () => void;
  assistantId: string;
  suggestion: string;
}) {
  const [text, setText] = useState(suggestion);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (open) {
      setText(suggestion);
      setSaved(false);
      setError("");
    }
  }, [open, suggestion]);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      await assistantsApi.addNuance(assistantId, text.trim());
      setSaved(true);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Добавить нюанс проверки" open={open} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Сформулируйте правило, которое модель должна была учесть. Оно добавится в нюансы дисциплины — при следующей
          генерации промпта архитектор его учтёт. Так закрывается петля «увидел ошибку → улучшил ассистента».
        </p>
        <Textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} />
        <ErrorNote message={error} />
        {saved && <p className="text-sm text-success">Добавлено в нюансы дисциплины</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Закрыть
          </Button>
          <Button onClick={submit} loading={loading} disabled={!text.trim() || saved}>
            Добавить нюанс
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function PipelineMode({ assistant }: { assistant: Assistant }) {
  const [pipelines, setPipelines] = useState<Pipeline[] | null>(null);
  const [pipelineId, setPipelineId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [taskText, setTaskText] = useState("");
  const [referenceSolution, setReferenceSolution] = useState("");
  const [manualRubric, setManualRubric] = useState<RubricCriterion[]>(() => rubricFromAssistant(assistant));
  const [manualMaxScore, setManualMaxScore] = useState(() => defaultMaxScore(rubricFromAssistant(assistant)));
  const [selectedTask, setSelectedTask] = useState<GeneratedTask | null>(null);
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const solution = useSolutionInput();
  const activeRubric = taskId ? selectedTask?.rubric ?? [] : manualRubric;
  const activeMaxScore = taskId ? selectedTask?.max_score ?? 0 : manualMaxScore;
  const rubricError = validateRubric(activeRubric, activeMaxScore);

  useEffect(() => {
    let cancelled = false;
    const nextRubric = rubricFromAssistant(assistant);
    setTaskId("");
    setTaskText("");
    setReferenceSolution("");
    setManualRubric(nextRubric);
    setManualMaxScore(defaultMaxScore(nextRubric));
    setSelectedTask(null);
    setRun(null);
    setError("");
    setPipelines(null);
    pipelinesApi
      .list(assistant.id)
      .then((list) => {
        if (cancelled) return;
        setPipelines(list);
        setPipelineId(list[0]?.id ?? "");
      })
      .catch((err) => {
        if (cancelled) return;
        setPipelines([]);
        setPipelineId("");
        setError(apiErrorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [assistant.id]);

  const execute = async () => {
    if (rubricError) return;
    setRunning(true);
    setError("");
    setRun(null);
    try {
      const result = await pipelinesApi.run(assistant.id, pipelineId, {
        task_id: taskId || null,
        task_text: taskText,
        reference_solution: referenceSolution,
        rubric: normalizedRubric(activeRubric),
        max_score: activeMaxScore,
        ocr_text: solution.ocrText,
        image_ids: solution.imageIds,
      });
      setRun(result);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="p-5 space-y-4">
          <h2 className="text-sm font-semibold">Пайплайн и задача</h2>
          <Field label="Пайплайн">
            <Select value={pipelineId} onChange={(e) => setPipelineId(e.target.value)}>
              {pipelines === null && <option value="">— загружаем пайплайны —</option>}
              {pipelines?.length === 0 && <option value="">— создайте пайплайн у ассистента —</option>}
              {pipelines?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.steps.length} шагов)
                </option>
              ))}
            </Select>
          </Field>
          <TaskPicker
            assistant={assistant}
            taskId={taskId}
            setTaskId={setTaskId}
            taskText={taskText}
            setTaskText={setTaskText}
            referenceSolution={referenceSolution}
            setReferenceSolution={setReferenceSolution}
            rubric={manualRubric}
            setRubric={setManualRubric}
            maxScore={manualMaxScore}
            setMaxScore={setManualMaxScore}
            rubricError={rubricError}
            onSelectedTaskChange={setSelectedTask}
          />
        </Card>
        <Card className="p-5 space-y-4">
          <h2 className="text-sm font-semibold">Решение студента</h2>
          {solution.node}
          <p className="text-xs text-muted-foreground">
            Фото распознаётся при загрузке; пайплайн использует отредактированный текст и не оплачивает повторный OCR.
          </p>
        </Card>
      </div>

      <ErrorNote message={error} />
      <Button
        onClick={execute}
        loading={running}
        disabled={
          Boolean(rubricError) ||
          !pipelineId ||
          (!taskId && !taskText.trim()) ||
          (!solution.ocrText.trim() && solution.imageIds.length === 0)
        }
      >
        <Play className="h-4 w-4" /> Запустить пайплайн
      </Button>
      {running && <p className="text-xs text-muted-foreground">Шаги выполняются последовательно, OCR может занять пару минут...</p>}

      {run && <PipelineRunView run={run} />}
    </div>
  );
}

function PipelineRunView({ run }: { run: PipelineRun }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Результат</h2>
        {run.status === "completed" ? <Badge tone="success">завершён</Badge> : <Badge tone="destructive">ошибка</Badge>}
      </div>
      {run.error && <ErrorNote message={run.error} />}
      {run.steps_log.map((step) => (
        <Card key={step.index} className="p-4">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">
              {step.index + 1}. {step.type}
            </span>
            {step.status === "completed" ? <Badge tone="success">ok</Badge> : <Badge tone="destructive">fail</Badge>}
            <span className="text-xs text-muted-foreground">{(step.duration_ms / 1000).toFixed(1)} с</span>
          </div>
          <StepOutput step={step} />
        </Card>
      ))}
    </div>
  );
}

function StepOutput({ step }: { step: PipelineRun["steps_log"][number] }) {
  const output = step.output ?? {};
  if (step.type === "ocr") {
    return (
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-muted-foreground flex items-center gap-1">
          <ScanText className="h-3.5 w-3.5" /> OCR-текст ({String(output.source)})
        </summary>
        <pre className="mt-2 whitespace-pre-wrap rounded bg-muted p-3 font-mono max-h-64 overflow-y-auto">
          {String(output.ocr_text ?? "")}
        </pre>
      </details>
    );
  }
  if (step.type === "grade") {
    const result = output.result as Record<string, unknown> | undefined;
    if (!result) return <p className="mt-2 text-xs text-destructive">{String(output.error ?? "нет результата")}</p>;
    return (
      <div className="mt-2 text-xs space-y-1">
        <p>
          <span className="font-medium">{String(output.model)}</span> · промпт v{String(output.prompt_version)} ·{" "}
          <span className="font-semibold">
            {String(result.total_score)} / {String(result.max_score)}
          </span>
        </p>
        {typeof result.feedback === "string" && (
          <MathText inline className="text-muted-foreground line-clamp-3 block">{result.feedback}</MathText>
        )}
      </div>
    );
  }
  return (
    <div className="mt-2 text-xs space-y-1">
      <p>
        Средний балл: <span className="font-semibold">{String(output.average_score)}</span> · разброс {String(output.spread)} (
        {String(output.spread_pct)}%)
      </p>
      {Boolean(output.needs_teacher_review) && <Badge tone="warning">требует проверки преподавателем</Badge>}
    </div>
  );
}

const TASK_STATUS_ORDER: Record<string, number> = { approved: 0, validated: 1 };

function TutorMode({ assistant, providers }: { assistant: Assistant; providers: Provider[] }) {
  const production = useMemo(() => modelOptions(providers, true), [providers]);
  const [tasks, setTasks] = useState<GeneratedTask[]>([]);
  const [tutorPrompts, setTutorPrompts] = useState<PromptVersion[]>([]);
  const [taskId, setTaskId] = useState("");
  const [manualTask, setManualTask] = useState("");
  const [studentWork, setStudentWork] = useState("");
  const [modelEntryId, setModelEntryId] = useState("");
  const [promptVersionId, setPromptVersionId] = useState("");
  const [messages, setMessages] = useState<TutorMessage[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [rating, setRating] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [savedComment, setSavedComment] = useState("");
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [nuanceOpen, setNuanceOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<TutorRun[] | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const assistantIdRef = useRef(assistant.id);

  const resetDialog = () => {
    setMessages([]);
    setRunId(null);
    setRating(null);
    setComment("");
    setSavedComment("");
    setInput("");
    setError("");
  };

  useEffect(() => {
    assistantIdRef.current = assistant.id;
    tasksApi
      .list(assistant.id)
      .then((list) =>
        setTasks([...list].sort((a, b) => (TASK_STATUS_ORDER[a.status] ?? 2) - (TASK_STATUS_ORDER[b.status] ?? 2))),
      )
      .catch(() => {});
    promptsApi
      .list(assistant.id)
      .then((list) => {
        const tutor = list.filter((p) => p.role === "tutor");
        setTutorPrompts(tutor);
        setPromptVersionId(tutor.find((p) => p.status === "active")?.id ?? "");
      })
      .catch(() => {});
    resetDialog();
    setSending(false);
    setTaskId("");
    setManualTask("");
    setStudentWork("");
    setHistoryOpen(false);
    setHistory(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistant.id]);

  useEffect(() => {
    if (!modelEntryId && production[0]) setModelEntryId(production[0].id);
  }, [production, modelEntryId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [messages, sending]);

  const composedWork = () => {
    const parts: string[] = [];
    if (!taskId && manualTask.trim()) parts.push(`Условие задачи:\n${manualTask.trim()}`);
    if (studentWork.trim()) parts.push(studentWork.trim());
    return parts.join("\n\n");
  };

  const send = async () => {
    const text = input.trim();
    if (!text || !modelEntryId || sending) return;
    const sentAssistantId = assistant.id;
    const historyMsgs: TutorMessage[] = [...messages, { role: "user", content: text }];
    setMessages(historyMsgs);
    setInput("");
    setSending(true);
    setError("");
    try {
      const { run } = await tutorApi.chat(sentAssistantId, {
        run_id: runId,
        task_id: taskId || null,
        prompt_version_id: promptVersionId || null,
        model_entry_id: modelEntryId,
        student_work: composedWork(),
        messages: historyMsgs,
      });
      if (assistantIdRef.current !== sentAssistantId) return;
      setRunId(run.id);
      setMessages(run.messages);
      setRating(run.rating);
      setComment(run.comment);
      setSavedComment(run.comment);
    } catch (err) {
      if (assistantIdRef.current !== sentAssistantId) return;
      setError(apiErrorMessage(err));
      setMessages(messages);
      setInput(text);
    } finally {
      if (assistantIdRef.current === sentAssistantId) setSending(false);
    }
  };

  const sendFeedback = async (body: { rating?: number; comment?: string }) => {
    if (!runId) return;
    try {
      const run = await tutorApi.feedback(runId, body);
      setRating(run.rating);
      setComment(run.comment);
      setSavedComment(run.comment);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  const toggleHistory = async () => {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next) {
      setHistory(null);
      try {
        setHistory(await tutorApi.runs(assistant.id));
      } catch {
        setHistory([]);
      }
    }
  };

  const loadRun = (run: TutorRun) => {
    setRunId(run.id);
    setMessages(run.messages);
    setStudentWork(run.student_work);
    setTaskId(run.task_id && tasks.some((t) => t.id === run.task_id) ? run.task_id : "");
    setManualTask("");
    setPromptVersionId(
      run.prompt_version_id && tutorPrompts.some((p) => p.id === run.prompt_version_id) ? run.prompt_version_id : "",
    );
    setRating(run.rating);
    setComment(run.comment);
    setSavedComment(run.comment);
    setInput("");
    setError("");
  };

  const lastAssistantReply = [...messages].reverse().find((m) => m.role === "assistant")?.content ?? "";

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="p-5 space-y-4">
          <h2 className="text-sm font-semibold">Сценарий разбора</h2>
          <Field label="Задача из банка">
            <Select value={taskId} onChange={(e) => setTaskId(e.target.value)}>
              <option value="">— ввести условие вручную —</option>
              {tasks.map((t) => (
                <option key={t.id} value={t.id}>
                  {(t.topic ? `${t.topic} — ` : "") + t.statement.slice(0, 60)}
                </option>
              ))}
            </Select>
          </Field>
          {!taskId && (
            <Field label="Условие задачи (вручную)">
              <Textarea rows={3} value={manualTask} onChange={(e) => setManualTask(e.target.value)} />
            </Field>
          )}
          <Field label="Решение / вопрос студента" hint="Контекст, который ассистент будет разбирать в диалоге">
            <Textarea rows={4} value={studentWork} onChange={(e) => setStudentWork(e.target.value)} />
          </Field>
          <Field label="Модель">
            <Select value={modelEntryId} onChange={(e) => setModelEntryId(e.target.value)}>
              {production.length === 0 && <option value="">— подключите провайдера —</option>}
              {production.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Версия tutor-промпта">
            <Select value={promptVersionId} onChange={(e) => setPromptVersionId(e.target.value)}>
              <option value="">— активная версия / встроенный —</option>
              {tutorPrompts.map((p) => (
                <option key={p.id} value={p.id}>
                  v{p.version} · {p.target_family}
                  {p.status === "active" ? " · активен" : ""}
                </option>
              ))}
            </Select>
          </Field>
          <Button variant="secondary" onClick={resetDialog} disabled={messages.length === 0 && runId === null}>
            <RotateCcw className="h-4 w-4" /> Новый диалог
          </Button>
        </Card>

        <Card className="p-5 space-y-3 lg:col-span-2 flex flex-col">
          <div>
            <h2 className="text-sm font-semibold">Диалог со студентом</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Вы играете роль студента — проверьте, как ассистент объясняет ошибки: в тех ли терминах и на данных курса
            </p>
          </div>
          <div className="flex-1 space-y-3 overflow-y-auto max-h-[28rem] pr-1">
            {messages.length === 0 && !sending && (
              <EmptyState
                title="Диалог не начат"
                hint="Напишите сообщение от лица студента — вопрос или фрагмент решения"
              />
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[85%] rounded-lg px-3 py-2 ${
                    m.role === "user" ? "bg-accent/10" : "bg-card border border-border"
                  }`}
                >
                  <MathText className="text-sm">{m.content}</MathText>
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> Ассистент отвечает...
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
          <ErrorNote message={error} />
          <div className="flex items-end gap-2">
            <Textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                  e.preventDefault();
                  void send();
                }
              }}
              placeholder="Сообщение от лица студента... (Ctrl/Cmd+Enter — отправить)"
              className="font-sans"
            />
            <Button onClick={send} loading={sending} disabled={!input.trim() || !modelEntryId}>
              <Send className="h-4 w-4" /> Отправить
            </Button>
          </div>
          {runId !== null && lastAssistantReply && (
            <div className="border-t border-border pt-3 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex gap-0.5">
                  {[1, 2, 3, 4, 5].map((star) => (
                    <button key={star} onClick={() => void sendFeedback({ rating: star })}>
                      <Star
                        className={`h-4 w-4 ${
                          rating && star <= rating ? "fill-warning text-warning" : "text-muted-foreground"
                        }`}
                      />
                    </button>
                  ))}
                </div>
                <Button variant="ghost" className="text-xs" onClick={() => setNuanceOpen(true)}>
                  <Lightbulb className="h-3.5 w-3.5" /> Сохранить как нюанс
                </Button>
              </div>
              <Input
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Комментарий к диалогу — что объяснено хорошо, что нет"
                className="text-xs"
                onBlur={() => {
                  if (comment !== savedComment) void sendFeedback({ comment });
                }}
              />
            </div>
          )}
        </Card>
      </div>

      <div className="space-y-2">
        <Button variant="ghost" onClick={toggleHistory}>
          <History className="h-4 w-4" /> {historyOpen ? "Скрыть историю" : "Показать историю"}
        </Button>
        {historyOpen &&
          (history === null ? (
            <Spinner />
          ) : history.length === 0 ? (
            <EmptyState title="Диалогов ещё не было" hint="Оценённые разборы — датасет для улучшения tutor-промпта" />
          ) : (
            <div className="space-y-2">
              {history.map((run) => (
                <Card key={run.id} className="p-3 cursor-pointer hover:bg-muted/40" onClick={() => loadRun(run)}>
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm truncate">
                        {run.messages.find((m) => m.role === "user")?.content.slice(0, 120) || "—"}
                      </p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {new Date(run.created_at).toLocaleString("ru-RU")} · {run.model_id} · сообщений:{" "}
                        {run.messages.length}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {run.rating !== null && <Badge tone="warning">★ {run.rating}</Badge>}
                      {run.task_id && <Badge tone="info">задача из банка</Badge>}
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          ))}
      </div>

      <NuanceModal
        open={nuanceOpen}
        onClose={() => setNuanceOpen(false)}
        assistantId={assistant.id}
        suggestion={lastAssistantReply}
      />
    </div>
  );
}

function HistoryMode({ assistant }: { assistant: Assistant }) {
  const [runs, setRuns] = useState<PlaygroundRun[] | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [promptLabels, setPromptLabels] = useState<Record<string, string>>({});

  useEffect(() => {
    setRuns(null);
    setExpandedId(null);
    void Promise.all([playgroundApi.runs(assistant.id), promptsApi.list(assistant.id)])
      .then(([history, prompts]) => {
        setRuns(history);
        setPromptLabels(Object.fromEntries(prompts.map((prompt) => [prompt.id, `v${prompt.version} · ${prompt.target_family}`])));
      })
      .catch(() => setRuns([]));
  }, [assistant.id]);

  if (runs === null) return <Spinner />;
  if (runs.length === 0)
    return <EmptyState title="Запусков ещё не было" hint="История сравнений с вашими оценками — это датасет для улучшения промптов" />;

  return (
    <div className="space-y-3">
      {runs.map((run) => (
        <Card key={run.id} className="min-w-0 overflow-hidden">
          <button
            type="button"
            aria-expanded={expandedId === run.id}
            className="flex w-full items-start gap-3 p-4 text-left hover:bg-muted/30"
            onClick={() => setExpandedId((current) => (current === run.id ? null : run.id))}
          >
            {expandedId === run.id ? (
              <ChevronDown className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <ChevronRight className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">{run.task_text.slice(0, 120)}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {new Date(run.created_at).toLocaleString("ru-RU")} · {run.results.length} моделей · {run.prompt_version_id ? promptLabels[run.prompt_version_id] || "версия промпта недоступна" : "без выбранной версии"}
              </p>
            </div>
            <div className="hidden shrink-0 flex-wrap justify-end gap-1.5 sm:flex">
              {run.results.map((result) => (
                <Badge key={result.id} tone={result.is_winner ? "accent" : result.status === "failed" ? "destructive" : "default"}>
                  {result.is_winner && <Crown className="mr-1 h-3 w-3" />}
                  {result.model_id.split("/").pop()}: {result.output?.total_score ?? "—"}
                </Badge>
              ))}
            </div>
          </button>
          {expandedId === run.id && (
            <div className="space-y-4 border-t border-border p-4">
              <section className="grid gap-3 lg:grid-cols-2">
                <div className="rounded-md border border-border bg-muted/20 p-3">
                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Задача</p>
                  <MathText className="text-sm">{run.task_text}</MathText>
                </div>
                <div className="rounded-md border border-border bg-muted/20 p-3">
                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Решение студента после OCR</p>
                  <MathText className="text-sm">{run.ocr_text || "Текст решения не сохранён"}</MathText>
                </div>
              </section>
              <div className="grid gap-3 lg:grid-cols-2">
                {run.results.map((result) => (
                  <div key={result.id} className="rounded-md border border-border bg-card p-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <p className="text-sm font-semibold">{result.provider_name} · {result.model_id}</p>
                      {result.is_winner && <Badge tone="accent"><Crown className="mr-1 h-3 w-3" /> выбран преподавателем</Badge>}
                      {result.rating !== null && <Badge tone="warning">★ {result.rating}</Badge>}
                      <Badge tone={result.status === "failed" ? "destructive" : "success"}>
                        {result.status === "failed" ? "ошибка" : `${result.output?.total_score ?? "—"} / ${result.output?.max_score ?? run.max_score}`}
                      </Badge>
                    </div>
                    {result.error && <p className="mt-2 text-xs text-destructive">{result.error}</p>}
                    {result.output?.criteria_scores && result.output.criteria_scores.length > 0 && (
                      <ul className="mt-3 space-y-1.5 text-xs">
                        {result.output.criteria_scores.map((criterion, index) => (
                          <li key={`${criterion.criterion_name}-${index}`}>
                            <span className="font-medium">{criterion.criterion_name}: {criterion.score}/{criterion.max_score}</span>
                            {criterion.comment && <span className="text-muted-foreground"> — {criterion.comment}</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                    {result.output?.feedback && <p className="mt-3 text-xs text-muted-foreground">{result.output.feedback}</p>}
                    {result.feedback_comment && (
                      <p className="mt-3 border-t border-border pt-2 text-xs">
                        <span className="font-medium">Комментарий преподавателя:</span> {result.feedback_comment}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}
