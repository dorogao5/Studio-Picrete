import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";
import { apiErrorMessage, promptsApi, sheetsApi, tasksApi } from "../../lib/api";
import type {
  AnswerFormat,
  Assistant,
  ExampleTask,
  GeneratedTask,
  GeneratedTaskStatus,
  GenerationBatch,
  PromptVersion,
  Provider,
  ReferenceSheet,
  TaskKind,
  TaskTemplate,
  TaskValidation,
} from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Select, Spinner, Textarea } from "../../components/ui";
import MathText from "../../components/MathText";
import { modelOptions } from "./PromptsTab";

type Tone = "default" | "success" | "warning" | "destructive" | "info" | "accent";

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

const SHEET_KIND_LABELS: Record<string, string> = {
  data_table: "Таблица данных",
  glossary: "Глоссарий",
  conventions: "Обозначения",
  formulas: "Формулы",
  other: "Другое",
};

const STATUS_META: Record<GeneratedTaskStatus, { label: string; tone: Tone }> = {
  draft: { label: "черновик", tone: "default" },
  validated: { label: "прошла проверку", tone: "info" },
  needs_review: { label: "требует внимания", tone: "warning" },
  approved: { label: "одобрена", tone: "success" },
  rejected: { label: "отклонена", tone: "destructive" },
};

const FILTERS: Array<{ key: "all" | GeneratedTaskStatus; label: string }> = [
  { key: "all", label: "Все" },
  { key: "draft", label: "Черновики" },
  { key: "validated", label: "Прошли проверку" },
  { key: "needs_review", label: "Требуют внимания" },
  { key: "approved", label: "Одобрены" },
  { key: "rejected", label: "Отклонены" },
];

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

export default function TasksTab({ assistant, providers }: { assistant: Assistant; providers: Provider[] }) {
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [tasks, setTasks] = useState<GeneratedTask[] | null>(null);
  const [batches, setBatches] = useState<GenerationBatch[]>([]);
  const [sheets, setSheets] = useState<ReferenceSheet[]>([]);
  const [prompts, setPrompts] = useState<PromptVersion[]>([]);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"all" | GeneratedTaskStatus>("all");
  const [templateModal, setTemplateModal] = useState<{ open: boolean; template: TaskTemplate | null }>({
    open: false,
    template: null,
  });
  const [batchModal, setBatchModal] = useState<{ open: boolean; templateId: string }>({ open: false, templateId: "" });
  const [exportOpen, setExportOpen] = useState(false);
  const [bulkLoading, setBulkLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);

  const reloadTasks = async () => {
    try {
      setTasks(await tasksApi.list(assistant.id));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  const reload = async () => {
    try {
      const [tpl, tsk, bt, sh, pr] = await Promise.all([
        tasksApi.templates(assistant.id),
        tasksApi.list(assistant.id),
        tasksApi.batches(assistant.id),
        sheetsApi.list(assistant.id),
        promptsApi.list(assistant.id),
      ]);
      setTemplates(tpl);
      setTasks(tsk);
      setBatches(bt);
      setSheets(sh);
      setPrompts(pr);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    setInitialLoading(true);
    void reload().finally(() => setInitialLoading(false));
  }, [assistant.id]);

  useEffect(() => {
    const running = batches.filter((b) => b.status === "running");
    if (running.length === 0) return;
    const timer = setInterval(async () => {
      try {
        const updated = await Promise.all(running.map((b) => tasksApi.batch(assistant.id, b.id)));
        setBatches((prev) => prev.map((b) => updated.find((u) => u.id === b.id) ?? b));
        if (updated.some((u) => u.status !== "running")) void reloadTasks();
      } catch {
        // сеть моргнула — попробуем на следующем тике
      }
    }, 2500);
    return () => clearInterval(timer);
  }, [batches, assistant.id]);

  const taskList = tasks ?? [];
  const validatedCount = taskList.filter((t) => t.status === "validated").length;
  const approvedCount = taskList.filter((t) => t.status === "approved").length;
  const filtered = filter === "all" ? taskList : taskList.filter((t) => t.status === filter);

  const approveAllValidated = async () => {
    const validated = taskList.filter((t) => t.status === "validated");
    if (validated.length === 0) return;
    if (!confirm(`Одобрить задачи, прошедшие проверку (${validated.length} шт.)?`)) return;
    setBulkLoading(true);
    try {
      await Promise.all(validated.map((t) => tasksApi.update(assistant.id, t.id, { status: "approved" })));
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      await reloadTasks();
      setBulkLoading(false);
    }
  };

  if (initialLoading) return <Spinner label="Загружаем блюпринты, партии и банк задач…" />;

  return (
    <div className="space-y-6">
      <ErrorNote message={error} />

      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Типовые задачи (блюпринты)</h2>
          <Button variant="secondary" onClick={() => setTemplateModal({ open: true, template: null })}>
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
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      className="p-1 text-muted-foreground hover:text-foreground"
                      title="Редактировать"
                      aria-label={`Редактировать блюпринт «${template.name}»`}
                      onClick={() => setTemplateModal({ open: true, template })}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      className="p-1 text-muted-foreground hover:text-destructive"
                      title="Удалить"
                      aria-label={`Удалить блюпринт «${template.name}»`}
                      onClick={async () => {
                        if (!confirm(`Удалить блюпринт «${template.name}»?`)) return;
                        try {
                          await tasksApi.removeTemplate(assistant.id, template.id);
                          setTemplates(await tasksApi.templates(assistant.id));
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
                    onClick={() => setBatchModal({ open: true, templateId: template.id })}
                  >
                    <Sparkles className="h-3.5 w-3.5" /> Сгенерировать партию
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Партии генерации</h2>
          <Button variant="ghost" className="text-xs" onClick={() => setBatchModal({ open: true, templateId: "" })}>
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

      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <h2 className="text-sm font-semibold">Банк задач</h2>
          <div className="flex items-center gap-2">
            {validatedCount > 0 && (
              <Button variant="secondary" onClick={approveAllValidated} loading={bulkLoading}>
                <CheckCircle2 className="h-3.5 w-3.5" /> Одобрить все прошедшие проверку ({validatedCount})
              </Button>
            )}
            <Button variant="secondary" onClick={() => setExportOpen(true)}>
              <Download className="h-3.5 w-3.5" /> Экспорт в Picrete
            </Button>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {FILTERS.map((f) => {
            const count = f.key === "all" ? taskList.length : taskList.filter((t) => t.status === f.key).length;
            return (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`rounded-full px-3 py-1 text-xs font-medium border ${
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
            title={filter === "all" ? "Задач пока нет" : "Нет задач с таким статусом"}
            hint={filter === "all" ? "Создайте блюпринт и сгенерируйте партию — задачи попадут сюда после автопроверки" : undefined}
          />
        ) : (
          <div className="space-y-2">
            {filtered.map((task) => (
              <TaskCard key={task.id} task={task} assistantId={assistant.id} onChanged={reloadTasks} />
            ))}
          </div>
        )}
      </section>

      {templateModal.open && (
        <TemplateModal
          assistant={assistant}
          sheets={sheets}
          template={templateModal.template}
          onClose={() => setTemplateModal({ open: false, template: null })}
          onSaved={async () => {
            setTemplateModal({ open: false, template: null });
            try {
              setTemplates(await tasksApi.templates(assistant.id));
            } catch (err) {
              setError(apiErrorMessage(err));
            }
          }}
        />
      )}
      {batchModal.open && (
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
          }}
        />
      )}
      {exportOpen && <ExportModal assistant={assistant} approvedCount={approvedCount} onClose={() => setExportOpen(false)} />}
    </div>
  );
}

function BatchCard({ batch, templates }: { batch: GenerationBatch; templates: TaskTemplate[] }) {
  const template = templates.find((t) => t.id === batch.template_id);
  const total = batch.progress.total ?? batch.requested_count;
  const done = batch.progress.done ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          {batch.status === "running" && <Badge tone="info">выполняется</Badge>}
          {batch.status === "completed" && <Badge tone="success">завершена</Badge>}
          {batch.status === "failed" && <Badge tone="destructive">ошибка</Badge>}
          <span className="text-sm truncate">{template ? template.name : "без блюпринта"}</span>
          <span className="text-xs text-muted-foreground">{batch.model_used}</span>
        </div>
        <span className="text-xs text-muted-foreground shrink-0">{new Date(batch.created_at).toLocaleString("ru-RU")}</span>
      </div>
      {batch.status === "running" && (
        <div className="mt-2 space-y-1.5">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{batch.progress.stage || "запуск..."}</span>
            <span>
              {done}/{total}
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}
      {batch.status === "completed" && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          сгенерировано: {batch.generated_count} из {batch.requested_count} · прошли проверку: {batch.validated_count}
        </p>
      )}
      {batch.status === "failed" && batch.error && <p className="mt-1.5 text-xs text-destructive">{batch.error}</p>}
    </Card>
  );
}

function ValidationReport({ task }: { task: GeneratedTask }) {
  const v: TaskValidation = task.validation ?? {};
  const hasReport = Boolean(v.solver || v.data || v.sanity || v.dedup);
  if (!hasReport) return <p className="text-xs text-muted-foreground">Проверка не выполнялась</p>;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5 flex-wrap">
        {v.solver?.status === "match" && <Badge tone="success">Решатель: совпал ✓</Badge>}
        {v.solver?.status === "mismatch" && <Badge tone="destructive">Решатель: расходится</Badge>}
        {v.solver?.status === "uncertain" && <Badge tone="warning">Решатель: не уверен</Badge>}
        {v.solver?.status === "error" && <Badge tone="destructive">Решатель: ошибка</Badge>}
        {v.solver?.status === "skipped" && <Badge>Решатель: пропущен</Badge>}
        {v.data?.status === "ok" && <Badge tone="success">Данные: ок</Badge>}
        {v.data?.status === "warn" && <Badge tone="warning">Данные: есть числа не из справочников</Badge>}
        {v.data?.status === "skipped" && <Badge>Данные: пропущено</Badge>}
        {v.sanity && ((v.sanity.issues?.length ?? 0) === 0 ? <Badge tone="success">Sanity: ок</Badge> : <Badge tone="warning">Sanity: есть замечания</Badge>)}
        {v.dedup?.duplicate && <Badge tone="warning">Дубликат</Badge>}
      </div>
      {(v.reasons?.length ?? 0) > 0 && (
        <ul className="list-disc pl-4 space-y-0.5">
          {v.reasons!.map((reason, i) => (
            <li key={i} className="text-xs text-muted-foreground break-words">
              <MathText inline>{reason}</MathText>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TaskCard({ task, assistantId, onChanged }: { task: GeneratedTask; assistantId: string; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [revalidating, setRevalidating] = useState(false);
  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalReason, setApprovalReason] = useState("");
  const [error, setError] = useState("");
  const status = STATUS_META[task.status] ?? STATUS_META.draft;

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
        <span className="text-xs text-muted-foreground">· {task.model_used}</span>
      </div>
      <button className="flex items-start gap-2 w-full text-left" onClick={() => setExpanded(!expanded)}>
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 mt-0.5" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 mt-0.5" />
        )}
        <span className={`text-sm ${expanded ? "" : "line-clamp-3"}`}>
          <MathText inline>{task.statement}</MathText>
        </span>
      </button>

      {expanded && (
        <div className="mt-3 ml-6 space-y-3 text-sm">
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
        {task.status !== "approved" ? (
          task.status === "validated" ? (
            <Button variant="secondary" loading={updatingStatus} onClick={() => setStatus("approved")}>
              <CheckCircle2 className="h-3.5 w-3.5" /> Одобрить
            </Button>
          ) : (
            <Button variant="secondary" disabled={updatingStatus} onClick={() => setApprovalOpen((open) => !open)}>
              <CheckCircle2 className="h-3.5 w-3.5" /> Ручное одобрение
            </Button>
          )
        ) : (
          <Button variant="ghost" loading={updatingStatus} onClick={() => setStatus("draft")}>
            <RefreshCw className="h-3.5 w-3.5" /> Вернуть в черновики
          </Button>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          {task.status !== "rejected" && (
            <button
              className="p-1.5 text-muted-foreground hover:text-foreground"
              title="Отклонить"
              onClick={() => setStatus("rejected")}
            >
              <XCircle className="h-4 w-4" />
            </button>
          )}
          <button
            className="p-1.5 text-muted-foreground hover:text-foreground disabled:opacity-40"
            title="Перепроверить автопроверкой"
            disabled={revalidating}
            onClick={revalidate}
          >
            {revalidating ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          </button>
          <button
            className="p-1.5 text-muted-foreground hover:text-foreground"
            title="Редактировать"
            onClick={() => setEditOpen(true)}
          >
            <Pencil className="h-4 w-4" />
          </button>
          <button
            className="p-1.5 text-muted-foreground hover:text-destructive"
            title="Удалить"
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
      {approvalOpen && task.status !== "approved" && (
        <div className="mt-3 rounded-lg border border-warning/40 bg-warning/5 p-3">
          <p className="text-sm font-medium">Почему задачу можно принять без зелёной автопроверки?</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Причина сохранится в истории задачи. Перед одобрением проверьте условие, решение, ответ и рубрику.
          </p>
          <Textarea
            className="mt-3"
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
              Подтвердить одобрение
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
  const [instructions, setInstructions] = useState(template?.instructions ?? "");
  const [kbQuery, setKbQuery] = useState(template?.kb_query ?? "");
  const [sheetIds, setSheetIds] = useState<string[]>(template?.reference_sheet_ids ?? []);
  const [examples, setExamples] = useState<ExampleTask[]>(template?.example_tasks ?? []);
  const [validationSolver, setValidationSolver] = useState(template?.validation_solver ?? true);
  const [validationDataCheck, setValidationDataCheck] = useState(template?.validation_data_check ?? true);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

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
        reference_sheet_ids: sheetIds,
        example_tasks: examples,
        kb_query: kbQuery,
        validation_solver: validationSolver,
        validation_data_check: validationDataCheck,
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
                <label key={sheet.id} className="flex items-center gap-2 text-sm">
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
                    className="p-1 text-muted-foreground hover:text-destructive"
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
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={validationSolver}
              onChange={(e) => setValidationSolver(e.target.checked)}
              className="h-4 w-4 accent-accent"
            />
            Перепроверка независимым решателем
          </label>
          <label className="flex items-center gap-2 text-sm">
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
          <Button onClick={submit} loading={loading} disabled={!name.trim()}>
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
  const generatorPrompts = useMemo(() => prompts.filter((p) => p.role === "generator"), [prompts]);
  const preferredGeneratorId = production.some((model) => model.id === assistant.default_generator_model_id)
    ? assistant.default_generator_model_id!
    : (production[0]?.id ?? "");
  const preferredSolverId = production.some((model) => model.id === assistant.default_grader_model_id)
    ? assistant.default_grader_model_id!
    : (production.find((model) => model.id !== preferredGeneratorId)?.id ?? preferredGeneratorId);
  const [templateId, setTemplateId] = useState(initialTemplateId);
  const [modelId, setModelId] = useState(preferredGeneratorId);
  const [solverId, setSolverId] = useState(preferredSolverId);
  const [promptVersionId, setPromptVersionId] = useState("");
  const [topic, setTopic] = useState("");
  const [difficulty, setDifficulty] = useState(initialTemplateId ? "" : "medium");
  const [count, setCount] = useState(5);
  const [temperature, setTemperature] = useState(0.7);
  const [validateTasks, setValidateTasks] = useState(true);
  const [instructions, setInstructions] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
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
        temperature,
        instructions,
        validate_tasks: validateTasks,
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
          label="Решатель"
          hint="DeepSeek Pro решает задачу дважды независимо. Flash — только предварительная проверка и не даёт зелёный статус."
        >
          <Select value={solverId} onChange={(e) => setSolverId(e.target.value)}>
            <option value="">— та же модель —</option>
            {production.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </Select>
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
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Сколько задач (1–20)">
            <Input type="number" min={1} max={20} value={count} onChange={(e) => setCount(Number(e.target.value))} />
          </Field>
          <Field label="Temperature">
            <Input
              type="number"
              min={0}
              max={2}
              step={0.1}
              value={temperature}
              onChange={(e) => setTemperature(Number(e.target.value))}
            />
          </Field>
        </div>
        <Field label="Доп. инструкции">
          <Textarea rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={validateTasks}
            onChange={(e) => setValidateTasks(e.target.checked)}
            className="h-4 w-4 accent-accent"
          />
          Автопроверка после генерации (решатель, сверка данных, sanity, дедуп)
        </label>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={!modelId}>
            <Sparkles className="h-4 w-4" /> Запустить партию
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ExportModal({
  assistant,
  approvedCount,
  onClose,
}: {
  assistant: Assistant;
  approvedCount: number;
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
        task_ids: [],
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
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="export-mode"
              checked={mode === "bank"}
              onChange={() => setMode("bank")}
              className="accent-accent"
            />
            Банк задач (тренажёр)
          </label>
          <label className="flex items-center gap-2 text-sm">
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
          Будут экспортированы одобренные задачи: {approvedCount} шт.
          {approvedCount === 0 ? " Сначала одобрите задачи в банке." : ""}
        </p>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={approvedCount === 0}>
            <Download className="h-4 w-4" /> Скачать JSON
          </Button>
        </div>
      </div>
    </Modal>
  );
}
