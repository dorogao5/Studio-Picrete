import { type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";
import { apiErrorMessage, promptsApi, sheetsApi, tasksApi } from "../../lib/api";
import { isKnownAdvisoryModel } from "../../lib/modelPolicy";
import {
  exportReadyTaskIds,
  nextRevalidationTaskIds,
  REVALIDATION_TASK_LIMIT,
  taskIsAutoReady,
  taskIsManualReady,
  taskNeedsAttention,
} from "../../lib/taskExport";
import type {
  AnswerFormat,
  Assistant,
  ChemistryCheckId,
  ExampleTask,
  GeneratedTask,
  GeneratedTaskStatus,
  GenerationBatch,
  PromptVersion,
  Provider,
  ReferenceSheet,
  RubricCriterion,
  TaskKind,
  TaskTemplate,
  TaskValidation,
} from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Select, Spinner, Textarea } from "../../components/ui";
import MathText from "../../components/MathText";
import { RubricEditor, rubricValidationError } from "../../components/RubricEditor";
import { modelOptions } from "./PromptsTab";

type Tone = "default" | "success" | "warning" | "destructive" | "info" | "accent";
type TasksSection = "templates" | "batches" | "bank";
type TaskFilter = "all" | "ready" | "attention" | "draft" | "rejected";

const KIND_LABELS: Record<TaskKind, string> = {
  calculation: "Расчётная задача",
  conceptual: "Теоретический вопрос",
  test_tf: "Тест «верно-неверно»",
  test_mc: "Тест с выбором",
  derivation: "Вывод формулы",
};

const FORMAT_LABELS: Record<AnswerFormat, string> = {
  numeric: "Число",
  formula: "Формула",
  text: "Текст",
  choice: "Выбор варианта",
};

const DIFF_LABELS: Record<string, string> = { easy: "лёгкая", medium: "средняя", hard: "сложная" };

const CHEMISTRY_CHECK_LABELS: Record<ChemistryCheckId, string> = {
  auto: "Определять автоматически",
  "chemistry.stoichiometry": "Стехиометрия и лимитирующий реагент",
  "chemistry.dilution": "Материальный баланс разбавления",
  "analytical.titration": "Эквивалентность титрования",
  "analytical.faraday": "Закон Фарадея",
  "analytical.calibration": "Калибровочная зависимость",
  "analytical.gravimetry": "Гравиметрический фактор",
  "analytical.conductometry": "Кондуктометрия",
  "colloid.bet": "Модель БЭТ",
  "colloid.smoluchowski": "Уравнение Смолуховского",
  "colloid.dlvo": "Модель ДЛФО",
};

const CHEMISTRY_EVIDENCE_LABELS: Record<string, string> = {
  "chemistry.units": "Единицы и размерности",
  "chemistry.reaction_balance": "Баланс уравнений",
  "chemistry.scale_compatibility": "Совместимость химических шкал",
  "chemistry.stoichiometry": "Стехиометрия",
  "chemistry.dilution": "Материальный баланс разбавления",
  "analytical.titration": "Эквивалентность титрования",
  "analytical.faraday": "Закон Фарадея",
  "analytical.calibration": "Калибровочная зависимость",
  "analytical.gravimetry": "Гравиметрический материальный баланс",
  "analytical.conductometry": "Сопротивление, постоянная ячейки и проводимость",
  "colloid.bet": "Модель БЭТ",
  "colloid.smoluchowski": "Уравнение Смолуховского",
  "colloid.dlvo": "Модель ДЛФО",
  "chemistry.core_calculation_uncovered": "Основной расчёт не покрыт",
  "chemistry.facts_schema": "Структура расчёта",
};

const chemistryChecksForDiscipline = (discipline: string): ChemistryCheckId[] => {
  const normalized = discipline.toLocaleLowerCase("ru-RU");
  const common: ChemistryCheckId[] = ["auto", "chemistry.stoichiometry", "chemistry.dilution"];
  if (normalized.includes("аналит")) {
    return [
      ...common,
      "analytical.titration",
      "analytical.faraday",
      "analytical.calibration",
      "analytical.gravimetry",
      "analytical.conductometry",
    ];
  }
  if (normalized.includes("коллоид") || normalized.includes("поверхност")) {
    return ["auto", "colloid.bet", "colloid.smoluchowski", "colloid.dlvo"];
  }
  return common;
};

const SHEET_KIND_LABELS: Record<string, string> = {
  data_table: "Таблица данных",
  glossary: "Глоссарий",
  conventions: "Обозначения",
  formulas: "Формулы",
  other: "Другое",
};

const FILTERS: Array<{ key: TaskFilter; label: string }> = [
  { key: "ready", label: "Готовы" },
  { key: "attention", label: "Требуют внимания" },
  { key: "all", label: "Все" },
  { key: "draft", label: "Черновики" },
  { key: "rejected", label: "Отклонены" },
];

const TASKS_PER_PAGE = 10;

const TRANSLIT: Record<string, string> = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y",
  к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f",
  х: "h", ц: "ts", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
};

function slugify(text: string): string {
  let out = "";
  for (const ch of text.toLowerCase()) out += TRANSLIT[ch] ?? ch;
  return out.replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "course";
}

function taskStatusMeta(task: GeneratedTask): { label: string; tone: Tone } {
  if (taskIsAutoReady(task)) return { label: "готова автоматически", tone: "success" };
  if (taskIsManualReady(task)) return { label: "принята как исключение", tone: "info" };
  if (taskNeedsAttention(task)) {
    if (task.status === "validated" || task.status === "approved") {
      return { label: "нужна перепроверка", tone: "warning" };
    }
    if (task.status === "draft") return { label: "не проверена", tone: "default" };
    return { label: "требует внимания", tone: "warning" };
  }
  return { label: "отклонена", tone: "destructive" };
}

export default function TasksTab({ assistant, providers }: { assistant: Assistant; providers: Provider[] }) {
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [tasks, setTasks] = useState<GeneratedTask[] | null>(null);
  const [batches, setBatches] = useState<GenerationBatch[]>([]);
  const [sheets, setSheets] = useState<ReferenceSheet[] | null>(null);
  const [prompts, setPrompts] = useState<PromptVersion[] | null>(null);
  const [sheetsLoading, setSheetsLoading] = useState(false);
  const [promptsLoading, setPromptsLoading] = useState(false);
  const [sheetsError, setSheetsError] = useState("");
  const [promptsError, setPromptsError] = useState("");
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<TaskFilter>("ready");
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);
  const [section, setSection] = useState<TasksSection | null>(null);
  const [templateModal, setTemplateModal] = useState<{ open: boolean; template: TaskTemplate | null }>({
    open: false,
    template: null,
  });
  const [batchModal, setBatchModal] = useState<{ open: boolean; templateId: string }>({ open: false, templateId: "" });
  const [exportOpen, setExportOpen] = useState(false);
  const [revalidationLoading, setRevalidationLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const assistantIdRef = useRef(assistant.id);
  const sheetsRequestRef = useRef(0);
  const promptsRequestRef = useRef(0);

  // Keep async responses from a previously selected assistant out of the current tab.
  assistantIdRef.current = assistant.id;

  const reloadTasks = async () => {
    const requestedAssistantId = assistant.id;
    try {
      const nextTasks = await tasksApi.list(requestedAssistantId);
      if (assistantIdRef.current === requestedAssistantId) setTasks(nextTasks);
    } catch (err) {
      if (assistantIdRef.current === requestedAssistantId) setError(apiErrorMessage(err));
    }
  };

  const reloadTemplates = async () => {
    const requestedAssistantId = assistant.id;
    try {
      const nextTemplates = await tasksApi.templates(requestedAssistantId);
      if (assistantIdRef.current === requestedAssistantId) setTemplates(nextTemplates);
    } catch (err) {
      if (assistantIdRef.current === requestedAssistantId) setError(apiErrorMessage(err));
    }
  };

  const loadSheets = async () => {
    const requestedAssistantId = assistant.id;
    const requestId = ++sheetsRequestRef.current;
    setSheetsLoading(true);
    setSheetsError("");
    try {
      const nextSheets = await sheetsApi.list(requestedAssistantId);
      if (assistantIdRef.current === requestedAssistantId && sheetsRequestRef.current === requestId) {
        setSheets(nextSheets);
      }
    } catch (err) {
      if (assistantIdRef.current === requestedAssistantId && sheetsRequestRef.current === requestId) {
        setSheetsError(apiErrorMessage(err));
      }
    } finally {
      if (assistantIdRef.current === requestedAssistantId && sheetsRequestRef.current === requestId) {
        setSheetsLoading(false);
      }
    }
  };

  const loadPrompts = async () => {
    const requestedAssistantId = assistant.id;
    const requestId = ++promptsRequestRef.current;
    setPromptsLoading(true);
    setPromptsError("");
    try {
      const nextPrompts = await promptsApi.list(requestedAssistantId);
      if (assistantIdRef.current === requestedAssistantId && promptsRequestRef.current === requestId) {
        setPrompts(nextPrompts);
      }
    } catch (err) {
      if (assistantIdRef.current === requestedAssistantId && promptsRequestRef.current === requestId) {
        setPromptsError(apiErrorMessage(err));
      }
    } finally {
      if (assistantIdRef.current === requestedAssistantId && promptsRequestRef.current === requestId) {
        setPromptsLoading(false);
      }
    }
  };

  const openTemplateModal = (template: TaskTemplate | null) => {
    setTemplateModal({ open: true, template });
    if (sheets === null && !sheetsLoading) void loadSheets();
  };

  const openBatchModal = (templateId: string) => {
    setBatchModal({ open: true, templateId });
    if (prompts === null && !promptsLoading) void loadPrompts();
  };

  useEffect(() => {
    const requestedAssistantId = assistant.id;
    let cancelled = false;

    sheetsRequestRef.current += 1;
    promptsRequestRef.current += 1;
    setTemplates([]);
    setTasks(null);
    setBatches([]);
    setSheets(null);
    setPrompts(null);
    setSheetsLoading(false);
    setPromptsLoading(false);
    setSheetsError("");
    setPromptsError("");
    setError("");
    setFilter("ready");
    setSearchQuery("");
    setPage(1);
    setSection(null);
    setTemplateModal({ open: false, template: null });
    setBatchModal({ open: false, templateId: "" });
    setExportOpen(false);
    setRevalidationLoading(false);
    setInitialLoading(true);
    void Promise.all([
      tasksApi.templates(requestedAssistantId),
      tasksApi.list(requestedAssistantId),
      tasksApi.batches(requestedAssistantId),
    ])
      .then(([nextTemplates, nextTasks, nextBatches]) => {
        if (cancelled || assistantIdRef.current !== requestedAssistantId) return;
        setTemplates(nextTemplates);
        setTasks(nextTasks);
        setBatches(nextBatches);
        setSection(nextTasks.length > 0 ? "bank" : "templates");
      })
      .catch((err) => {
        if (!cancelled && assistantIdRef.current === requestedAssistantId) setError(apiErrorMessage(err));
      })
      .finally(() => {
        if (!cancelled && assistantIdRef.current === requestedAssistantId) setInitialLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [assistant.id]);

  useEffect(() => {
    const running = batches.filter((b) => b.status === "running");
    if (running.length === 0) return;
    const requestedAssistantId = assistant.id;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const updated = await Promise.all(running.map((b) => tasksApi.batch(requestedAssistantId, b.id)));
        if (cancelled || assistantIdRef.current !== requestedAssistantId) return;
        setBatches((prev) => prev.map((b) => updated.find((u) => u.id === b.id) ?? b));
        if (updated.some((u) => u.status !== "running")) void reloadTasks();
      } catch {
        // сеть моргнула — попробуем на следующем тике
      }
    }, 2500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [batches, assistant.id]);

  const taskList = tasks ?? [];
  const exportTaskIds = exportReadyTaskIds(taskList);
  const autoReadyCount = taskList.filter(taskIsAutoReady).length;
  const manualReadyCount = taskList.filter(taskIsManualReady).length;
  const attentionTasks = taskList.filter(taskNeedsAttention);
  const attentionCount = attentionTasks.length;
  const nextAttentionTaskIds = nextRevalidationTaskIds(attentionTasks);
  const attentionAfterNextBatch = Math.max(0, attentionCount - nextAttentionTaskIds.length);
  const runningRevalidation = batches.find(
    (batch) => batch.status === "running" && batch.params.operation === "revalidation",
  );
  const excludedExportCount = taskList.length - exportTaskIds.length;
  const statusFiltered =
    filter === "all"
      ? taskList
      : filter === "ready"
        ? taskList.filter((task) => task.export_ready)
        : filter === "attention"
          ? attentionTasks
          : taskList.filter((task) => task.status === filter);
  const normalizedQuery = searchQuery.trim().toLocaleLowerCase("ru-RU");
  const filtered = normalizedQuery
    ? statusFiltered.filter((task) =>
        [task.statement, task.topic, task.model_used].some((value) =>
          value?.toLocaleLowerCase("ru-RU").includes(normalizedQuery),
        ),
      )
    : statusFiltered;
  const pageCount = Math.max(1, Math.ceil(filtered.length / TASKS_PER_PAGE));
  const currentPage = Math.min(page, pageCount);
  const pageStart = (currentPage - 1) * TASKS_PER_PAGE;
  const visibleTasks = filtered.slice(pageStart, pageStart + TASKS_PER_PAGE);
  const activeSection = section ?? (taskList.length > 0 ? "bank" : "templates");
  const sections: Array<{ key: TasksSection; label: string; count: number }> = [
    { key: "templates", label: "Блюпринты", count: templates.length },
    { key: "batches", label: "Партии", count: batches.length },
    { key: "bank", label: "Банк задач", count: taskList.length },
  ];

  useEffect(() => {
    setPage(1);
  }, [filter, searchQuery, assistant.id, taskList.length]);

  const revalidateAttentionQueue = async () => {
    if (nextAttentionTaskIds.length === 0) return;
    setRevalidationLoading(true);
    setError("");
    try {
      const batch = await tasksApi.createRevalidationBatch(assistant.id, { task_ids: nextAttentionTaskIds });
      setBatches((previous) => [batch, ...previous.filter((item) => item.id !== batch.id)]);
      setSection("batches");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setRevalidationLoading(false);
    }
  };

  if (initialLoading) return <Spinner label="Загружаем блюпринты, партии и банк задач…" />;

  return (
    <div className="space-y-6">
      <ErrorNote message={error} />

      <nav
        aria-label="Разделы заданий"
        className="grid w-full grid-cols-3 gap-1 rounded-lg border border-border bg-muted/40 p-1"
      >
        {sections.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={activeSection === item.key}
            onClick={() => setSection(item.key)}
            className={`flex min-h-10 min-w-0 items-center justify-center gap-1.5 rounded-md px-2 py-2 text-xs font-medium transition-colors sm:text-sm ${
              activeSection === item.key
                ? "bg-card text-foreground shadow-soft"
                : "text-muted-foreground hover:bg-card/60 hover:text-foreground"
            }`}
          >
            <span className="truncate">{item.label}</span>
            <span className="shrink-0 tabular-nums text-[11px] text-muted-foreground">{item.count}</span>
          </button>
        ))}
      </nav>

      {activeSection === "templates" && (
        <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Типовые задачи (блюпринты)</h2>
          <Button variant="secondary" onClick={() => openTemplateModal(null)}>
            <Plus className="h-4 w-4" /> Новый блюпринт
          </Button>
        </div>
        {templates.length === 0 ? (
          <EmptyState
            title="Блюпринтов пока нет"
            hint="Блюпринт — описание типовой задачи: вид, формат ответа, справочники, примеры из задачника"
          />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {templates.map((template) => (
              <Card key={template.id} className="min-w-0 overflow-hidden p-3.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{template.name}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {template.topic || "без темы"} · {DIFF_LABELS[template.difficulty] ?? template.difficulty}
                    </p>
                    <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                      <Badge tone="info">{KIND_LABELS[template.task_kind] ?? template.task_kind}</Badge>
                      <span className="text-xs text-muted-foreground">
                        примеров: {template.example_tasks.length} · справочников: {template.reference_sheet_ids.length}
                      </span>
                    </div>
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      Предметный контроль: {CHEMISTRY_CHECK_LABELS[template.chemistry_check ?? "auto"]}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      className="inline-flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                      title="Редактировать"
                      aria-label={`Редактировать блюпринт «${template.name}»`}
                      onClick={() => openTemplateModal(template)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      className="inline-flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      title="Удалить"
                      aria-label={`Удалить блюпринт «${template.name}»`}
                      onClick={async () => {
                        if (!confirm(`Удалить блюпринт «${template.name}»?`)) return;
                        try {
                          await tasksApi.removeTemplate(assistant.id, template.id);
                          await reloadTemplates();
                        } catch (err) {
                          setError(apiErrorMessage(err));
                        }
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
                <div className="mt-2.5">
                  <Button
                    variant="accent"
                    className="w-full sm:w-auto"
                    onClick={() => openBatchModal(template.id)}
                  >
                    <Sparkles className="h-3.5 w-3.5" /> Сгенерировать партию
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}
        </section>
      )}

      {activeSection === "batches" && (
        <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Партии генерации</h2>
          <Button variant="ghost" className="text-xs" onClick={() => openBatchModal("")}>
            <Sparkles className="h-3.5 w-3.5" /> Генерация по теме, без блюпринта
          </Button>
        </div>
        {batches.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Здесь появится история генераций. Запустите партию с карточки блюпринта — задачи пройдут автопроверку
            (независимый решатель, сверка данных, дедуп) и попадут в банк.
          </p>
        ) : (
          <div className="space-y-2">
            {batches.map((batch) => (
              <BatchCard key={batch.id} batch={batch} templates={templates} />
            ))}
          </div>
        )}
        </section>
      )}

      {activeSection === "bank" && (
        <section className="space-y-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <h2 className="text-sm font-semibold">Банк задач</h2>
          <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
            {attentionCount > 0 && (
              <div className="min-w-0">
                <Button
                  variant="secondary"
                  loading={revalidationLoading}
                  disabled={Boolean(runningRevalidation)}
                  onClick={revalidateAttentionQueue}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  {runningRevalidation
                    ? "Перепроверка выполняется"
                    : attentionCount > REVALIDATION_TASK_LIMIT
                      ? `Перепроверить следующие ${REVALIDATION_TASK_LIMIT}`
                      : `Перепроверить ${attentionCount}`}
                </Button>
                {attentionAfterNextBatch > 0 && !runningRevalidation && (
                  <p className="mt-1 text-[11px] tabular-nums text-muted-foreground" aria-live="polite">
                    За один запуск — {nextAttentionTaskIds.length}; затем останется {attentionAfterNextBatch}
                  </p>
                )}
              </div>
            )}
            <Button variant="secondary" onClick={() => setExportOpen(true)}>
              <Download className="h-3.5 w-3.5" /> Экспорт в Picrete
            </Button>
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <Card className="p-3.5">
            <p className="text-xs font-medium text-muted-foreground">Готовы к использованию</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums text-foreground">{exportTaskIds.length}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Автоматически: {autoReadyCount} · приняты как исключение: {manualReadyCount}
            </p>
          </Card>
          <Card className={attentionCount > 0 ? "border-warning/35 p-3.5" : "p-3.5"}>
            <p className="text-xs font-medium text-muted-foreground">Требуют решения</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums text-foreground">{attentionCount}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Здесь только расхождения, устаревшие проверки и незавершённые черновики
            </p>
          </Card>
        </div>

        <p className="rounded-lg border border-border bg-muted/20 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
          Задача становится готовой без ручного подтверждения, только если два независимых решения DeepSeek Pro,
          предметный критик, сверка ответа и единиц, источники, рубрика и проверка на дубликаты дали согласованный результат.
          Кандидаты, не прошедшие этот контур, отбрасываются и не попадают в очередь преподавателя.
          Очередь внимания содержит только сохранённые задачи, которым нужна повторная проверка или решение об исключении.
        </p>

        <div role="search" className="w-full sm:max-w-sm">
          <label className="relative block">
            <span className="sr-only">Поиск по банку задач</span>
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Поиск по условию, теме или модели"
              className="min-w-0 pl-9"
            />
          </label>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {FILTERS.map((f) => {
            const count =
              f.key === "all"
                ? taskList.length
                : f.key === "ready"
                  ? exportTaskIds.length
                  : f.key === "attention"
                    ? attentionCount
                    : taskList.filter((task) => task.status === f.key).length;
            return (
              <button
                key={f.key}
                type="button"
                aria-pressed={filter === f.key}
                onClick={() => setFilter(f.key)}
                className={`min-h-10 rounded-full border px-3 py-1 text-xs font-medium ${
                  filter === f.key
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-border text-muted-foreground hover:bg-muted"
                }`}
              >
                {f.label} · {count}
              </button>
            );
          })}
        </div>

        {tasks === null ? (
          <Spinner />
        ) : filtered.length === 0 ? (
          <EmptyState
            title={
              normalizedQuery
                ? "По вашему запросу ничего не найдено"
                : filter === "ready"
                  ? "Готовых задач пока нет"
                  : filter === "all"
                  ? "Задач пока нет"
                  : "Нет задач с таким статусом"
            }
            hint={
              normalizedQuery
                ? "Попробуйте изменить запрос или выбрать другой статус"
                : filter === "ready"
                  ? "Запустите перепроверку банка или сгенерируйте партию по сертифицированному блюпринту"
                  : filter === "all"
                  ? "Создайте блюпринт и сгенерируйте партию — задачи попадут сюда после автопроверки"
                  : undefined
            }
          />
        ) : (
          <div className="min-w-0 space-y-3">
            <div className="min-w-0 space-y-2">
              {visibleTasks.map((task) => (
                <TaskCard key={task.id} task={task} assistantId={assistant.id} onChanged={reloadTasks} />
              ))}
            </div>
            <nav
              aria-label="Страницы банка задач"
              className="flex min-w-0 flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <p className="text-xs tabular-nums text-muted-foreground" aria-live="polite">
                {pageStart + 1}–{Math.min(pageStart + TASKS_PER_PAGE, filtered.length)} из {filtered.length}
              </p>
              <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto">
                <Button
                  variant="secondary"
                  className="w-full sm:w-auto"
                  disabled={currentPage === 1}
                  onClick={() => setPage((previous) => Math.max(1, previous - 1))}
                >
                  <ChevronLeft className="h-4 w-4" /> Назад
                </Button>
                <Button
                  variant="secondary"
                  className="w-full sm:w-auto"
                  disabled={currentPage === pageCount}
                  onClick={() => setPage((previous) => Math.min(pageCount, previous + 1))}
                >
                  Вперёд <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </nav>
          </div>
        )}
        </section>
      )}

      {templateModal.open && sheets === null && (
        <Modal
          title={templateModal.template ? "Редактирование блюпринта" : "Новый блюпринт"}
          open
          onClose={() => setTemplateModal({ open: false, template: null })}
          wide
        >
          {sheetsLoading ? (
            <Spinner label="Загружаем справочники курса…" />
          ) : (
            <div className="space-y-4">
              <ErrorNote message={sheetsError || "Не удалось загрузить справочники курса"} />
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setTemplateModal({ open: false, template: null })}>
                  Закрыть
                </Button>
                <Button onClick={() => void loadSheets()} loading={sheetsLoading}>
                  <RefreshCw className="h-3.5 w-3.5" /> Повторить
                </Button>
              </div>
            </div>
          )}
        </Modal>
      )}
      {templateModal.open && sheets !== null && (
        <TemplateModal
          assistant={assistant}
          sheets={sheets}
          template={templateModal.template}
          onClose={() => setTemplateModal({ open: false, template: null })}
          onSaved={async () => {
            setTemplateModal({ open: false, template: null });
            setSection("templates");
            try {
              await reloadTemplates();
            } catch (err) {
              setError(apiErrorMessage(err));
            }
          }}
        />
      )}
      {batchModal.open && prompts === null && (
        <Modal title="Партия генерации" open onClose={() => setBatchModal({ open: false, templateId: "" })}>
          {promptsLoading ? (
            <Spinner label="Загружаем версии промптов…" />
          ) : (
            <div className="space-y-4">
              <ErrorNote message={promptsError || "Не удалось загрузить версии промптов"} />
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setBatchModal({ open: false, templateId: "" })}>
                  Закрыть
                </Button>
                <Button onClick={() => void loadPrompts()} loading={promptsLoading}>
                  <RefreshCw className="h-3.5 w-3.5" /> Повторить
                </Button>
              </div>
            </div>
          )}
        </Modal>
      )}
      {batchModal.open && prompts !== null && (
        <BatchLaunchModal
          assistant={assistant}
          providers={providers}
          templates={templates}
          prompts={prompts}
          initialTemplateId={batchModal.templateId}
          onClose={() => setBatchModal({ open: false, templateId: "" })}
          onLaunched={(batch) => {
            setBatchModal({ open: false, templateId: "" });
            setBatches((prev) => [batch, ...prev]);
            setSection("batches");
          }}
        />
      )}
      {exportOpen && (
        <ExportModal
          assistant={assistant}
          taskIds={exportTaskIds}
          excludedCount={excludedExportCount}
          attentionCount={attentionCount}
          autoReadyCount={autoReadyCount}
          manualReadyCount={manualReadyCount}
          onClose={() => setExportOpen(false)}
        />
      )}
    </div>
  );
}

function BatchCard({ batch, templates }: { batch: GenerationBatch; templates: TaskTemplate[] }) {
  const template = templates.find((t) => t.id === batch.template_id);
  const isRevalidation = batch.params.operation === "revalidation";
  const total = batch.progress.total ?? batch.requested_count;
  const done = batch.progress.done ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const rawQuality = batch.params.quality_summary;
  const quality = rawQuality && typeof rawQuality === "object" && !Array.isArray(rawQuality)
    ? rawQuality as Record<string, unknown>
    : {};
  const qualityCount = (key: string, fallback: number) =>
    typeof quality[key] === "number" ? quality[key] as number : fallback;
  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          {batch.status === "running" && <Badge tone="info">выполняется</Badge>}
          {batch.status === "completed" && <Badge tone="success">завершена</Badge>}
          {batch.status === "failed" && <Badge tone="destructive">ошибка</Badge>}
          <span className="text-sm truncate">
            {isRevalidation ? "Перепроверка банка" : template ? template.name : "без блюпринта"}
          </span>
          <span className="text-xs text-muted-foreground">{batch.model_used}</span>
        </div>
        <span className="text-xs text-muted-foreground shrink-0">{new Date(batch.created_at).toLocaleString("ru-RU")}</span>
      </div>
      {batch.status === "running" && (
        <div className="mt-2 space-y-1.5">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span aria-live="polite">{batch.progress.stage || "запуск..."}</span>
            <span>
              {done}/{total}
            </span>
          </div>
          <div
            role="progressbar"
            aria-label={isRevalidation ? "Ход перепроверки банка" : "Ход генерации партии"}
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={done}
            aria-valuetext={`${done} из ${total}`}
            className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
          >
            <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}
      {batch.status === "completed" && (
        <p className="mt-1.5 text-xs text-muted-foreground" role="status" aria-live="polite">
          {isRevalidation
            ? `проверено: ${batch.generated_count} · готовы: ${qualityCount("ready_count", batch.validated_count)} · исключены: ${qualityCount("discarded_count", 0)} · требуют внимания: ${qualityCount("attention_count", Math.max(0, batch.generated_count - batch.validated_count))}`
            : `готовы: ${batch.validated_count} из ${batch.requested_count} · проверено кандидатов: ${batch.generated_count} · отброшено: ${Math.max(0, batch.generated_count - batch.validated_count)}`}
        </p>
      )}
      {batch.status === "failed" && batch.error && <p className="mt-1.5 text-xs text-destructive">{batch.error}</p>}
    </Card>
  );
}

type EvidenceState = "success" | "warning" | "error" | "neutral";

function EvidenceMark({ state }: { state: EvidenceState }) {
  if (state === "success") return <CheckCircle2 className="h-4 w-4 shrink-0 text-success" aria-hidden />;
  if (state === "error") return <XCircle className="h-4 w-4 shrink-0 text-destructive" aria-hidden />;
  if (state === "warning") return <XCircle className="h-4 w-4 shrink-0 text-warning" aria-hidden />;
  return <span className="h-4 w-4 shrink-0 text-center text-muted-foreground" aria-hidden>—</span>;
}

function EvidenceGroup({ title, summary, state, children }: {
  title: string;
  summary: string;
  state: EvidenceState;
  children?: ReactNode;
}) {
  return (
    <details className="group border-t border-border first:border-t-0" open={state === "error" || state === "warning"}>
      <summary className="flex min-h-12 cursor-pointer list-none items-start gap-2.5 py-3 marker:content-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
        <EvidenceMark state={state} />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium leading-4">{title}</span>
          <span className="mt-1 block text-xs leading-4 text-muted-foreground">{summary}</span>
        </span>
        <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden />
      </summary>
      {children && <div className="pb-3 pl-6 pr-6 text-xs text-muted-foreground">{children}</div>}
    </details>
  );
}

function ChemistryEvidence({ chemistry }: { chemistry: NonNullable<TaskValidation["chemistry"]> }) {
  const results = chemistry.results ?? [];
  const applicable = results.filter((result) => result.state !== "not_applicable");
  const notApplicable = results.filter((result) => result.state === "not_applicable");
  return (
    <div className="space-y-2">
      {applicable.map((result) => {
        const state: EvidenceState = result.state === "pass" ? "success" : result.state === "warning" ? "warning" : "error";
        return (
          <div key={result.check_id} className="flex items-start gap-2 rounded-md bg-muted/35 px-2.5 py-2">
            <EvidenceMark state={state} />
            <div className="min-w-0">
              <p className="font-medium text-foreground">{CHEMISTRY_EVIDENCE_LABELS[result.check_id] ?? result.check_id}</p>
              <p className="mt-0.5 leading-4">{result.message}</p>
            </div>
          </div>
        );
      })}
      {applicable.length === 0 && <p>Для этого задания не найдено проверяемого предметного инварианта.</p>}
      {notApplicable.length > 0 && (
        <details>
          <summary className="min-h-10 cursor-pointer py-2 text-muted-foreground">
            Ещё {notApplicable.length} неприменимых проверок
          </summary>
          <ul className="space-y-1 pl-4">
            {notApplicable.map((result) => (
              <li key={result.check_id}>{CHEMISTRY_EVIDENCE_LABELS[result.check_id] ?? result.check_id}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ValidationReport({ task }: { task: GeneratedTask }) {
  const v: TaskValidation = task.validation ?? {};
  const hasReport = Boolean(v.solver || v.verifier || v.critic || v.chemistry || v.data || v.sanity || v.dedup || v.approval);
  if (!hasReport) return <p className="text-xs text-muted-foreground">Проверка не выполнялась</p>;
  const solutionsPass = v.solver?.status === "match" && v.verifier?.status === "match"
    && v.cross_comparison?.verdict === "match" && v.critic?.status === "pass";
  const chemistryEffect = v.chemistry?.admission_effect;
  const chemistryState: EvidenceState = chemistryEffect === "pass" ? "success"
    : chemistryEffect === "block" ? "error" : chemistryEffect === "limited" ? "warning" : "neutral";
  const requiredChecks = v.chemistry?.required_check_ids ?? [];
  const passedRequired = requiredChecks.filter((checkId) =>
    v.chemistry?.results?.some((result) => result.check_id === checkId && result.state === "pass"),
  ).length;
  const firstChemistryProblem = v.chemistry?.admission_reason
    ?? v.chemistry?.results?.find((result) => ["fail", "warning", "indeterminate", "error"].includes(result.state))?.message;
  const dataPass = v.data?.status === "ok" && v.source_lineage?.status === "ok"
    && !(v.data.unknown_numbers?.length || v.data.unknown_sources?.length || v.source_lineage.unbound_sources?.length);
  const integrityPass = v.reference_solution_check?.verdict === "match"
    && (v.sanity?.issues?.length ?? 0) === 0 && v.dedup?.duplicate === false;
  const manualException = task.export_ready && v.approval?.basis === "teacher_override";

  return (
    <div className="space-y-2">
      <div className={`rounded-lg border px-3 py-2.5 ${task.export_ready ? "border-success/35 bg-success/5" : "border-border bg-muted/20"}`}>
        <div className="flex items-start gap-2">
          <EvidenceMark state={task.export_ready ? "success" : "error"} />
          <div>
            <p className="text-sm font-semibold">
              {manualException ? "Принята как ручное исключение" : task.export_ready ? "Допущена автоматически" : "Не допущена"}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {manualException
                ? v.approval?.reason ?? "Преподаватель зафиксировал обоснованное исключение из автоматической политики"
                : task.export_ready
                ? "Доказательства согласованы; ручное подтверждение не требуется"
                : v.reasons?.[0] ?? "Есть незавершённые обязательные проверки"}
            </p>
          </div>
        </div>
      </div>
      <div className="rounded-lg border border-border px-3">
        <EvidenceGroup
          title="Независимое решение"
          state={solutionsPass ? "success" : "error"}
          summary={solutionsPass ? "Два решения и предметный критик согласованы" : "Контрольные решения не дали единого доказательства"}
        >
          <p>Основной решатель: {v.solver?.status ?? "не запускался"}; аудитор: {v.verifier?.status ?? "не запускался"}; критик: {v.critic?.status ?? "не запускался"}.</p>
        </EvidenceGroup>
        <EvidenceGroup
          title="Химическая корректность"
          state={chemistryState}
          summary={chemistryEffect === "pass"
            ? `${passedRequired} из ${requiredChecks.length} обязательных проверок подтверждены`
            : firstChemistryProblem ?? "Детерминированное покрытие ограничено"}
        >
          {v.chemistry ? <ChemistryEvidence chemistry={v.chemistry} /> : <p>Предметный пакет ещё не запускался.</p>}
        </EvidenceGroup>
        <EvidenceGroup
          title="Данные и источники"
          state={dataPass ? "success" : "error"}
          summary={dataPass ? "Числа и ссылки подтверждены" : "Происхождение данных подтверждено не полностью"}
        >
          {(v.data?.unknown_numbers?.length ?? 0) > 0 && <p>Не подтверждены числа: {v.data!.unknown_numbers!.join(", ")}</p>}
          {(v.data?.unknown_sources?.length ?? 0) > 0 && <p>Не найдены источники: {v.data!.unknown_sources!.join(", ")}</p>}
          {(v.source_lineage?.unbound_sources?.length ?? 0) > 0 && (
            <p>Нет связи с исходным документом: {v.source_lineage!.unbound_sources!.join(", ")}</p>
          )}
        </EvidenceGroup>
        <EvidenceGroup
          title="Эталон, рубрика и дубликаты"
          state={integrityPass ? "success" : "error"}
          summary={integrityPass ? "Эталон полный, рубрика согласована, дубликатов нет" : "Структурная проверка задания не завершена"}
        >
          {(v.sanity?.issues ?? []).map((issue, index) => <p key={index}>{issue}</p>)}
          {v.dedup?.duplicate && <p>Найдено сходство с существующим заданием.</p>}
          {v.reference_solution_check?.verdict !== "match" && <p>Эталонное решение не содержит полный финальный ответ.</p>}
        </EvidenceGroup>
      </div>
      {(v.reasons?.length ?? 0) > 1 && (
        <details className="text-xs text-muted-foreground">
          <summary className="min-h-10 cursor-pointer py-2">Все причины ({v.reasons!.length})</summary>
          <ul className="list-disc space-y-0.5 pl-5">
            {v.reasons!.map((reason, index) => <li key={index}><MathText inline>{reason}</MathText></li>)}
          </ul>
        </details>
      )}
      {v.approval && (
        <div className="rounded-md border border-warning/35 bg-warning/5 p-2.5 text-xs">
          <p className="font-medium">{task.export_ready ? "Принято преподавателем как исключение" : "Ручное решение требует обновления"}</p>
          {v.approval.reason && <p className="mt-1 text-muted-foreground">{v.approval.reason}</p>}
        </div>
      )}
    </div>
  );
}

function TaskCard({ task, assistantId, onChanged }: { task: GeneratedTask; assistantId: string; onChanged: () => void }) {
  const detailsId = useId();
  const approvalId = useId();
  const [expanded, setExpanded] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [revalidating, setRevalidating] = useState(false);
  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalReason, setApprovalReason] = useState("");
  const [error, setError] = useState("");
  const status = taskStatusMeta(task);
  const needsAttention = taskNeedsAttention(task);
  const isAutoReady = taskIsAutoReady(task);
  const isManualReady = taskIsManualReady(task);

  const setStatus = async (next: GeneratedTaskStatus, approvalReasonOverride = "") => {
    setUpdatingStatus(true);
    setError("");
    try {
      await tasksApi.update(assistantId, task.id, { status: next, approval_reason: approvalReasonOverride });
      setApprovalOpen(false);
      setApprovalReason("");
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setUpdatingStatus(false);
    }
  };

  const revalidate = async () => {
    setRevalidating(true);
    setError("");
    try {
      await tasksApi.revalidate(assistantId, task.id);
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setRevalidating(false);
    }
  };

  return (
    <Card className="p-4">
      <div className="flex items-center gap-1.5 flex-wrap mb-2">
        <Badge tone={status.tone}>{status.label}</Badge>
        <Badge tone="info">{DIFF_LABELS[task.difficulty] ?? task.difficulty}</Badge>
        {task.topic && <span className="text-xs text-muted-foreground">{task.topic}</span>}
      </div>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={detailsId}
        className="flex min-h-10 w-full items-start gap-2 rounded-md py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 mt-0.5" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 mt-0.5" />
        )}
        <span className={`text-sm ${expanded ? "" : "line-clamp-3"}`}>
          <MathText inline>{task.statement}</MathText>
        </span>
      </button>
      {task.validation?.chemistry && (
        <div className="ml-6 flex items-start gap-1.5 text-xs text-muted-foreground">
          <EvidenceMark state={task.validation.chemistry.admission_effect === "pass"
            ? "success"
            : task.validation.chemistry.admission_effect === "limited" ? "warning" : "error"} />
          <span>
            {task.validation.chemistry.admission_effect === "pass"
              ? `Предметный контроль: подтверждено ${task.validation.chemistry.required_check_ids?.length ?? 0}`
              : task.validation.chemistry.admission_effect === "limited"
                ? "Общие химические проверки пройдены; специальный расчётный инвариант не требуется"
              : task.validation.chemistry.admission_reason ?? "Предметный контроль требует внимания"}
          </span>
        </div>
      )}

      {expanded && (
        <div id={detailsId} className="mt-3 ml-6 space-y-3 text-sm">
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase mb-1">Эталонное решение</p>
            <MathText className="text-muted-foreground">{task.reference_solution || "—"}</MathText>
          </div>
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase mb-1">Ответ</p>
            <span className="inline-block rounded bg-success/10 border border-success/30 px-2 py-0.5 font-medium text-success">
              <MathText inline>{task.answer || "—"}</MathText>
            </span>
          </div>
          {task.rubric.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase mb-1">Рубрика (макс. {task.max_score} б.)</p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <tbody>
                    {task.rubric.map((r, i) => (
                      <tr key={i} className="border-t border-border">
                        <td className="py-1 pr-3 font-medium align-top">
                          <MathText inline>{r.criterion_name}</MathText>
                        </td>
                        <td className="py-1 pr-3 text-muted-foreground align-top">
                          <MathText inline>{r.description ?? ""}</MathText>
                        </td>
                        <td className="py-1 text-right whitespace-nowrap align-top">{r.max_score} б.</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase mb-1">Автопроверка</p>
            <ValidationReport task={task} />
          </div>
          {(task.grounding?.sheets?.length ?? 0) > 0 && (
            <p className="text-xs text-muted-foreground">
              Справочники: {task.grounding.sheets!.map((s) => s.title).join(", ")}
              {(task.grounding.kb_chunks ?? 0) > 0 ? ` · выдержек из базы знаний: ${task.grounding.kb_chunks}` : ""}
            </p>
          )}
        </div>
      )}

      <ErrorNote message={error} />
      <div className="mt-3 flex items-center gap-1 flex-wrap">
        {isAutoReady && (
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-success">
            <CheckCircle2 className="h-3.5 w-3.5" /> Готова к Picrete
          </span>
        )}
        {isManualReady && (
          <>
            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-foreground">
              <CheckCircle2 className="h-3.5 w-3.5" /> Принята как исключение
            </span>
            <Button variant="ghost" loading={updatingStatus} disabled={revalidating} onClick={() => setStatus("draft")}>
              <RefreshCw className="h-3.5 w-3.5" /> Вернуть в черновики
            </Button>
          </>
        )}
        {needsAttention && (
          <Button variant="secondary" loading={revalidating} disabled={updatingStatus} onClick={revalidate}>
            <RefreshCw className="h-3.5 w-3.5" /> Перепроверить автоматически
          </Button>
        )}
        {task.status === "needs_review" && !isManualReady && (
          <Button
            variant="ghost"
            aria-expanded={approvalOpen}
            aria-controls={approvalId}
            disabled={updatingStatus || revalidating}
            onClick={() => setApprovalOpen((open) => !open)}
          >
            Принять как исключение
          </Button>
        )}
        {task.status === "rejected" && (
          <Button variant="ghost" loading={updatingStatus} disabled={revalidating} onClick={() => setStatus("draft")}>
            Вернуть в черновики
          </Button>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          {task.status !== "rejected" && (
            <button
              type="button"
              className="inline-flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              title="Отклонить"
              aria-label="Отклонить задачу"
              disabled={updatingStatus || revalidating}
              onClick={() => setStatus("rejected")}
            >
              <XCircle className="h-4 w-4" />
            </button>
          )}
          {!needsAttention && task.status !== "rejected" && (
            <button
              type="button"
              className="inline-flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
              title="Перепроверить автопроверкой"
              aria-label="Перепроверить задачу"
              disabled={revalidating || updatingStatus}
              onClick={revalidate}
            >
              {revalidating ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            </button>
          )}
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
            title="Редактировать"
            aria-label="Редактировать задачу"
            disabled={updatingStatus || revalidating}
            onClick={() => setEditOpen(true)}
          >
            <Pencil className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:pointer-events-none disabled:opacity-40"
            title="Удалить"
            aria-label="Удалить задачу"
            disabled={updatingStatus || revalidating}
            onClick={async () => {
              if (!confirm(`Удалить задачу?`)) return;
              try {
                await tasksApi.remove(assistantId, task.id);
                onChanged();
              } catch (err) {
                setError(apiErrorMessage(err));
              }
            }}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>
      {approvalOpen && task.status === "needs_review" && (
        <div id={approvalId} className="mt-3 rounded-lg border border-warning/40 bg-warning/5 p-3">
          <p className="text-sm font-medium">Почему задачу нужно принять вопреки автоматической проверке?</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Это исключение из автоматической политики. Причина и автор решения сохранятся в истории задачи.
          </p>
          <Textarea
            className="mt-3"
            autoFocus
            rows={2}
            maxLength={500}
            value={approvalReason}
            onChange={(event) => setApprovalReason(event.target.value)}
            placeholder="Например: проверено вручную по методичке, допустимое округление подтверждено"
          />
          <div className="mt-2 flex flex-wrap gap-2">
            <Button
              variant="secondary"
              loading={updatingStatus}
              disabled={approvalReason.trim().length < 10}
              onClick={() => setStatus("approved", approvalReason.trim())}
            >
              Принять как исключение
            </Button>
            <Button
              variant="ghost"
              disabled={updatingStatus}
              onClick={() => {
                setApprovalOpen(false);
                setApprovalReason("");
              }}
            >
              Отмена
            </Button>
          </div>
        </div>
      )}

      {editOpen && (
        <TaskEditModal
          task={task}
          assistantId={assistantId}
          onClose={() => setEditOpen(false)}
          onSaved={() => {
            setEditOpen(false);
            onChanged();
          }}
        />
      )}
    </Card>
  );
}

function TaskEditModal({
  task,
  assistantId,
  onClose,
  onSaved,
}: {
  task: GeneratedTask;
  assistantId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [statement, setStatement] = useState(task.statement);
  const [solution, setSolution] = useState(task.reference_solution);
  const [answer, setAnswer] = useState(task.answer);
  const [maxScore, setMaxScore] = useState(task.max_score);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      await tasksApi.update(assistantId, task.id, {
        statement,
        reference_solution: solution,
        answer,
        max_score: maxScore,
      });
      onSaved();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Редактирование задачи" open onClose={onClose} wide>
      <div className="space-y-4">
        <Field label="Условие">
          <Textarea rows={5} value={statement} onChange={(e) => setStatement(e.target.value)} />
        </Field>
        <Field label="Эталонное решение">
          <Textarea rows={6} value={solution} onChange={(e) => setSolution(e.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Ответ">
            <Input value={answer} onChange={(e) => setAnswer(e.target.value)} />
          </Field>
          <Field label="Макс. балл">
            <Input type="number" min={0} value={maxScore} onChange={(e) => setMaxScore(Number(e.target.value))} />
          </Field>
        </div>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={!statement.trim()}>
            Сохранить
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function TemplateModal({
  assistant,
  sheets,
  template,
  onClose,
  onSaved,
}: {
  assistant: Assistant;
  sheets: ReferenceSheet[];
  template: TaskTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(template?.name ?? "");
  const [topic, setTopic] = useState(template?.topic ?? "");
  const [taskKind, setTaskKind] = useState<TaskKind>(template?.task_kind ?? "calculation");
  const [difficulty, setDifficulty] = useState(template?.difficulty ?? "medium");
  const [answerFormat, setAnswerFormat] = useState<AnswerFormat>(template?.answer_format ?? "numeric");
  const [tolerance, setTolerance] = useState(template?.numeric_tolerance_pct ?? 2);
  const [rubric, setRubric] = useState<RubricCriterion[]>(template?.rubric ?? []);
  const [instructions, setInstructions] = useState(template?.instructions ?? "");
  const [kbQuery, setKbQuery] = useState(template?.kb_query ?? "");
  const [sheetIds, setSheetIds] = useState<string[]>(template?.reference_sheet_ids ?? []);
  const [examples, setExamples] = useState<ExampleTask[]>(template?.example_tasks ?? []);
  const [validationSolver, setValidationSolver] = useState(template?.validation_solver ?? true);
  const [validationDataCheck, setValidationDataCheck] = useState(template?.validation_data_check ?? true);
  const [chemistryCheck, setChemistryCheck] = useState<ChemistryCheckId>(template?.chemistry_check ?? "auto");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const rubricError = rubricValidationError(rubric);

  const toggleSheet = (id: string) =>
    setSheetIds((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));

  const updateExample = (index: number, patch: Partial<ExampleTask>) =>
    setExamples(examples.map((ex, i) => (i === index ? { ...ex, ...patch } : ex)));

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      const body = {
        name,
        topic,
        difficulty,
        instructions,
        task_kind: taskKind,
        answer_format: answerFormat,
        numeric_tolerance_pct: tolerance,
        rubric,
        reference_sheet_ids: sheetIds,
        example_tasks: examples,
        kb_query: kbQuery,
        validation_solver: validationSolver,
        validation_data_check: validationDataCheck,
        chemistry_check: chemistryCheck,
      };
      if (template) await tasksApi.updateTemplate(assistant.id, template.id, body);
      else await tasksApi.createTemplate(assistant.id, body);
      onSaved();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title={template ? "Редактирование блюпринта" : "Новый блюпринт"} open onClose={onClose} wide>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Название">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="напр. Расчёт pH буфера" />
          </Field>
          <Field label="Тема">
            <Input value={topic} onChange={(e) => setTopic(e.target.value)} list="tpl-topics" placeholder="Буферные растворы" />
            <datalist id="tpl-topics">
              {assistant.topics.map((t) => (
                <option key={t} value={t} />
              ))}
            </datalist>
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Вид задачи">
            <Select value={taskKind} onChange={(e) => setTaskKind(e.target.value as TaskKind)}>
              {(Object.keys(KIND_LABELS) as TaskKind[]).map((kind) => (
                <option key={kind} value={kind}>
                  {KIND_LABELS[kind]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Сложность">
            <Select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
              <option value="easy">лёгкая</option>
              <option value="medium">средняя</option>
              <option value="hard">сложная</option>
            </Select>
          </Field>
          <Field label="Формат ответа">
            <Select value={answerFormat} onChange={(e) => setAnswerFormat(e.target.value as AnswerFormat)}>
              {(Object.keys(FORMAT_LABELS) as AnswerFormat[]).map((fmt) => (
                <option key={fmt} value={fmt}>
                  {FORMAT_LABELS[fmt]}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {answerFormat === "numeric" && (
          <Field label="Допуск числового ответа, %" hint="В пределах допуска ответ решателя считается совпавшим">
            <Input
              type="number"
              min={0}
              max={50}
              step={0.5}
              value={tolerance}
              onChange={(e) => setTolerance(Number(e.target.value))}
              className="w-32"
            />
          </Field>
        )}
        <div className="rounded-lg border border-border bg-muted/15 p-3.5">
          <p className="text-sm font-medium">Предметный контроль</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            Платформа сама извлекает структуру расчёта и проверяет её формулами. Зафиксируйте тип только если
            этот блюпринт всегда строится на одном методе.
          </p>
          <div className="mt-3">
            <Field label="Тип расчёта">
              <Select value={chemistryCheck} onChange={(event) => setChemistryCheck(event.target.value as ChemistryCheckId)}>
                {chemistryChecksForDiscipline(assistant.discipline).map((checkId) => (
                  <option key={checkId} value={checkId}>{CHEMISTRY_CHECK_LABELS[checkId]}</option>
                ))}
              </Select>
            </Field>
          </div>
        </div>
        <RubricEditor value={rubric} onChange={setRubric} disabled={loading} />
        <Field
          label="Инструкции генерации"
          hint="Что варьировать, какие данные давать, чего избегать — это и есть «типовое задание»"
        >
          <Textarea
            rows={4}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="Задача на расчёт pH ацетатного буфера. Варьировать концентрации 0.01–1 М и соотношение кислота/соль..."
          />
        </Field>
        <Field label="Запрос к базе знаний" hint="По этому запросу подтянутся выдержки из материалов курса">
          <Input value={kbQuery} onChange={(e) => setKbQuery(e.target.value)} placeholder="буферные растворы pH расчёт" />
        </Field>
        <Field
          label="Привязанные справочники"
          hint="Их данные инжектятся в промпт генерации дословно. Ничего не выбрано — в генерацию попадут все канонические справочники дисциплины"
        >
          {sheets.length === 0 ? (
            <p className="text-xs text-muted-foreground">Добавьте справочные материалы на вкладке «Материалы курса»</p>
          ) : (
            <div className="space-y-1.5 rounded-md border border-border p-3 max-h-48 overflow-y-auto">
              {sheets.map((sheet) => (
                <label key={sheet.id} className="flex min-h-10 items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={sheetIds.includes(sheet.id)}
                    onChange={() => toggleSheet(sheet.id)}
                    className="h-4 w-4 accent-accent"
                  />
                  <Badge>{SHEET_KIND_LABELS[sheet.kind] ?? sheet.kind}</Badge>
                  <span className="truncate">{sheet.title}</span>
                </label>
              ))}
            </div>
          )}
        </Field>
        <Field label="Примеры из задачника" hint="Стиль и уровень примеров задают планку для генерации">
          <div className="space-y-2">
            {examples.map((ex, i) => (
              <div key={i} className="rounded-md border border-border p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-muted-foreground">Пример {i + 1}</span>
                  <button
                    type="button"
                    className="inline-flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    aria-label={`Удалить пример ${i + 1}`}
                    onClick={() => setExamples(examples.filter((_, idx) => idx !== i))}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                <Textarea
                  rows={3}
                  value={ex.statement}
                  onChange={(e) => updateExample(i, { statement: e.target.value })}
                  placeholder="Условие"
                />
                <Textarea
                  rows={3}
                  value={ex.solution}
                  onChange={(e) => updateExample(i, { solution: e.target.value })}
                  placeholder="Решение"
                />
                <Input value={ex.answer} onChange={(e) => updateExample(i, { answer: e.target.value })} placeholder="Ответ" />
              </div>
            ))}
            <Button
              variant="secondary"
              onClick={() => setExamples([...examples, { statement: "", solution: "", answer: "" }])}
            >
              <Plus className="h-3.5 w-3.5" /> Добавить пример
            </Button>
          </div>
        </Field>
        <div className="space-y-1.5">
          <label className="flex min-h-10 items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={validationSolver}
              onChange={(e) => setValidationSolver(e.target.checked)}
              className="h-4 w-4 accent-accent"
            />
            Перепроверка независимым решателем
          </label>
          <label className="flex min-h-10 items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={validationDataCheck}
              onChange={(e) => setValidationDataCheck(e.target.checked)}
              className="h-4 w-4 accent-accent"
            />
            Сверка чисел со справочниками
          </label>
        </div>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={!name.trim() || Boolean(rubricError)}>
            {template ? "Сохранить" : "Создать блюпринт"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function BatchLaunchModal({
  assistant,
  providers,
  templates,
  prompts,
  initialTemplateId,
  onClose,
  onLaunched,
}: {
  assistant: Assistant;
  providers: Provider[];
  templates: TaskTemplate[];
  prompts: PromptVersion[];
  initialTemplateId: string;
  onClose: () => void;
  onLaunched: (batch: GenerationBatch) => void;
}) {
  const production = useMemo(() => modelOptions(providers, true), [providers]);
  const controlModels = useMemo(() => production.filter((model) => !isKnownAdvisoryModel(model)), [production]);
  const hiddenAdvisoryCount = production.length - controlModels.length;
  const generatorPrompts = useMemo(() => prompts.filter((p) => p.role === "generator"), [prompts]);
  const preferredGeneratorId = production.some((model) => model.id === assistant.default_generator_model_id)
    ? assistant.default_generator_model_id!
    : (production[0]?.id ?? "");
  const preferredSolverId = controlModels.some((model) => model.id === assistant.default_grader_model_id)
    ? assistant.default_grader_model_id!
    : (controlModels.find((model) => model.modelId.toLocaleLowerCase() === "deepseek-v4-pro")?.id ??
      controlModels.find((model) => model.id !== preferredGeneratorId)?.id ??
      controlModels[0]?.id ??
      "");
  const [templateId, setTemplateId] = useState(initialTemplateId);
  const [modelId, setModelId] = useState(preferredGeneratorId);
  const [solverId, setSolverId] = useState(preferredSolverId);
  const [promptVersionId, setPromptVersionId] = useState("");
  const [topic, setTopic] = useState("");
  const [difficulty, setDifficulty] = useState(initialTemplateId ? "" : "medium");
  const [count, setCount] = useState(5);
  const [instructions, setInstructions] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!solverId) {
      setError("Нужна контрольная модель итогового класса — например DeepSeek V4 Pro. Flash не может допускать задачи в банк.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const batch = await tasksApi.createBatch(assistant.id, {
        template_id: templateId || null,
        model_entry_id: modelId,
        solver_model_entry_id: solverId || null,
        prompt_version_id: promptVersionId || null,
        topic,
        difficulty,
        count,
        temperature: 0.7,
        instructions,
        validate_tasks: true,
      });
      onLaunched(batch);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Партия генерации" open onClose={onClose}>
      <div className="space-y-4">
        <Field label="Блюпринт (необязательно)">
          <Select
            value={templateId}
            onChange={(e) => {
              const next = e.target.value;
              setTemplateId(next);
              setDifficulty(next ? "" : "medium");
            }}
          >
            <option value="">— без блюпринта, по теме —</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Производственная модель">
          <Select value={modelId} onChange={(e) => setModelId(e.target.value)}>
            {production.length === 0 && <option value="">— подключите production-провайдера —</option>}
            {production.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Контрольная модель"
          hint="Из списка скрыты модели с явной маркировкой Flash/advisory. Перед запуском сервер дополнительно сверит выбранную модель с актуальной policy и заблокирует партию до генерации, если она не имеет права итогового допуска."
        >
          <Select value={solverId} onChange={(e) => setSolverId(e.target.value)}>
            {controlModels.length === 0 && <option value="">— подключите модель итогового класса —</option>}
            {controlModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </Select>
          {hiddenAdvisoryCount > 0 && (
            <span className="block text-xs text-muted-foreground">
              Не показано предварительных моделей: {hiddenAdvisoryCount}. Они доступны в Playground, но не в контуре допуска.
            </span>
          )}
        </Field>
        <Field label="Версия промпта-генератора">
          <Select value={promptVersionId} onChange={(e) => setPromptVersionId(e.target.value)}>
            <option value="">— активная версия —</option>
            {generatorPrompts.map((p) => (
              <option key={p.id} value={p.id}>
                v{p.version}
                {p.status === "active" ? " (активна)" : ""}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Тема">
            <Input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              list="batch-topics"
              placeholder={templateId ? "из блюпринта" : ""}
            />
            <datalist id="batch-topics">
              {assistant.topics.map((t) => (
                <option key={t} value={t} />
              ))}
            </datalist>
          </Field>
          <Field label="Сложность">
            <Select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
              {templateId ? <option value="">— из блюпринта —</option> : null}
              <option value="easy">лёгкая</option>
              <option value="medium">средняя</option>
              <option value="hard">сложная</option>
            </Select>
          </Field>
        </div>
        <Field label="Сколько готовых задач нужно (1–20)">
          <Input type="number" min={1} max={20} value={count} onChange={(e) => setCount(Number(e.target.value))} />
        </Field>
        <Field label="Доп. инструкции">
          <Textarea rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
        </Field>
        <p className="rounded-lg border border-border bg-muted/20 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
          Платформа сама проверит каждый кандидат: два независимых решения, предметную критику,
          решаемость без скрытых данных, полный ответ и единицы, источники, рубрику и дубликаты.
          Готовые задачи попадут в банк без ручного подтверждения.
          Кандидаты с расхождениями будут отброшены и не попадут в очередь преподавателя.
        </p>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={!modelId || !solverId}>
            <Sparkles className="h-4 w-4" /> Запустить партию
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ExportModal({
  assistant,
  taskIds,
  excludedCount,
  attentionCount,
  autoReadyCount,
  manualReadyCount,
  onClose,
}: {
  assistant: Assistant;
  taskIds: string[];
  excludedCount: number;
  attentionCount: number;
  autoReadyCount: number;
  manualReadyCount: number;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<"bank" | "variants">("bank");
  const [sourceCode, setSourceCode] = useState(`studio_${slugify(assistant.discipline)}`);
  const [sourceTitle, setSourceTitle] = useState(assistant.discipline);
  const [version, setVersion] = useState("1.0");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      const data = await tasksApi.exportTasks(assistant.id, {
        task_ids: taskIds,
        mode,
        source_code: sourceCode,
        source_title: sourceTitle,
        version,
      });
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `picrete-tasks-${mode}.json`;
      a.click();
      URL.revokeObjectURL(url);
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Экспорт в Picrete" open onClose={onClose}>
      <div className="space-y-4">
        <div className="space-y-1.5">
          <span className="text-sm font-medium">Формат</span>
          <label className="flex min-h-10 items-center gap-2 text-sm">
            <input
              type="radio"
              name="export-mode"
              checked={mode === "bank"}
              onChange={() => setMode("bank")}
              className="accent-accent"
            />
            Банк задач (тренажёр)
          </label>
          <label className="flex min-h-10 items-center gap-2 text-sm">
            <input
              type="radio"
              name="export-mode"
              checked={mode === "variants"}
              onChange={() => setMode("variants")}
              className="accent-accent"
            />
            Варианты для контрольной
          </label>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Код источника">
            <Input value={sourceCode} onChange={(e) => setSourceCode(e.target.value)} />
          </Field>
          <Field label="Версия">
            <Input value={version} onChange={(e) => setVersion(e.target.value)} />
          </Field>
        </div>
        <Field label="Название источника">
          <Input value={sourceTitle} onChange={(e) => setSourceTitle(e.target.value)} />
        </Field>
        <p className="text-xs text-muted-foreground">
          В файл войдут готовые задачи: {taskIds.length} шт. Автоматически допущены: {autoReadyCount};
          приняты преподавателем как исключение: {manualReadyCount}.
        </p>
        {excludedCount > 0 && (
          <p className="rounded-md border border-warning/40 bg-warning/5 p-2.5 text-xs text-foreground">
            Не войдут в файл: {excludedCount} шт. Из них требуют решения: {attentionCount}. Черновики,
            отклонённые задачи и задачи без полного набора доказательств не экспортируются.
          </p>
        )}
        {taskIds.length === 0 && (
          <p className="text-xs text-muted-foreground">
            Пока нет ни одной задачи, прошедшей полный автоматический контроль или принятой как исключение.
          </p>
        )}
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={taskIds.length === 0}>
            <Download className="h-4 w-4" /> Скачать JSON
          </Button>
        </div>
      </div>
    </Modal>
  );
}
