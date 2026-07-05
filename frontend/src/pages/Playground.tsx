import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Crown, Lightbulb, Play, ScanText, Star, Upload } from "lucide-react";
import {
  apiErrorMessage,
  assistantsApi,
  pipelinesApi,
  playgroundApi,
  promptsApi,
  providersApi,
  tasksApi,
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
} from "../lib/types";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Select, Spinner, Tabs, Textarea } from "../components/ui";
import { modelOptions } from "./assistant/PromptsTab";

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

function TaskPicker({
  assistant,
  taskId,
  setTaskId,
  taskText,
  setTaskText,
  referenceSolution,
  setReferenceSolution,
}: {
  assistant: Assistant;
  taskId: string;
  setTaskId: (v: string) => void;
  taskText: string;
  setTaskText: (v: string) => void;
  referenceSolution: string;
  setReferenceSolution: (v: string) => void;
}) {
  const [tasks, setTasks] = useState<GeneratedTask[]>([]);

  useEffect(() => {
    tasksApi.list(assistant.id).then(setTasks).catch(() => {});
  }, [assistant.id]);

  return (
    <div className="space-y-3">
      <Field label="Задача из банка">
        <Select value={taskId} onChange={(e) => setTaskId(e.target.value)}>
          <option value="">— ввести условие вручную —</option>
          {tasks.map((t) => (
            <option key={t.id} value={t.id}>
              {(t.approved ? "✓ " : "") + t.statement.slice(0, 90)}
            </option>
          ))}
        </Select>
      </Field>
      {!taskId && (
        <>
          <Field label="Условие задачи">
            <Textarea rows={4} value={taskText} onChange={(e) => setTaskText(e.target.value)} />
          </Field>
          <Field label="Эталонное решение (желательно)">
            <Textarea rows={4} value={referenceSolution} onChange={(e) => setReferenceSolution(e.target.value)} />
          </Field>
        </>
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
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [run, setRun] = useState<PlaygroundRun | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const solution = useSolutionInput();

  useEffect(() => {
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
    setRunning(true);
    setError("");
    setRun(null);
    try {
      const result = await playgroundApi.compare({
        assistant_id: assistant.id,
        prompt_version_id: promptVersionId || null,
        task_id: taskId || null,
        task_text: taskText,
        reference_solution: referenceSolution,
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
          disabled={selectedModels.length === 0 || !solution.ocrText.trim() || (!taskId && !taskText.trim())}
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
              <p className="mt-1 whitespace-pre-wrap">{output.feedback}</p>
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
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [pipelineId, setPipelineId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [taskText, setTaskText] = useState("");
  const [referenceSolution, setReferenceSolution] = useState("");
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const solution = useSolutionInput();

  useEffect(() => {
    pipelinesApi.list(assistant.id).then((list) => {
      setPipelines(list);
      if (list[0]) setPipelineId(list[0].id);
    });
  }, [assistant.id]);

  const execute = async () => {
    setRunning(true);
    setError("");
    setRun(null);
    try {
      const result = await pipelinesApi.run(assistant.id, pipelineId, {
        task_id: taskId || null,
        task_text: taskText,
        reference_solution: referenceSolution,
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
              {pipelines.length === 0 && <option value="">— создайте пайплайн у ассистента —</option>}
              {pipelines.map((p) => (
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
          />
        </Card>
        <Card className="p-5 space-y-4">
          <h2 className="text-sm font-semibold">Решение студента</h2>
          {solution.node}
          <p className="text-xs text-muted-foreground">
            Если в пайплайне есть шаг OCR и вы загрузили фото — распознавание случится внутри пайплайна; уже
            распознанный текст выше имеет приоритет.
          </p>
        </Card>
      </div>

      <ErrorNote message={error} />
      <Button
        onClick={execute}
        loading={running}
        disabled={!pipelineId || (!taskId && !taskText.trim()) || (!solution.ocrText.trim() && solution.imageIds.length === 0)}
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
        {typeof result.feedback === "string" && <p className="text-muted-foreground line-clamp-3">{result.feedback}</p>}
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

function HistoryMode({ assistant }: { assistant: Assistant }) {
  const [runs, setRuns] = useState<PlaygroundRun[] | null>(null);

  useEffect(() => {
    playgroundApi.runs(assistant.id).then(setRuns).catch(() => setRuns([]));
  }, [assistant.id]);

  if (runs === null) return <Spinner />;
  if (runs.length === 0)
    return <EmptyState title="Запусков ещё не было" hint="История сравнений с вашими оценками — это датасет для улучшения промптов" />;

  return (
    <div className="space-y-3">
      {runs.map((run) => (
        <Card key={run.id} className="p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm truncate">{run.task_text.slice(0, 120)}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {new Date(run.created_at).toLocaleString("ru-RU")} · {run.results.length} моделей
              </p>
            </div>
            <div className="flex gap-1.5 shrink-0 flex-wrap justify-end">
              {run.results.map((result) => (
                <Badge key={result.id} tone={result.is_winner ? "accent" : result.status === "failed" ? "destructive" : "default"}>
                  {result.is_winner && <Crown className="h-3 w-3 mr-1" />}
                  {result.model_id.split("/").pop()}: {result.output?.total_score ?? "—"}
                </Badge>
              ))}
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}
