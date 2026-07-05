import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Plus, Sparkles, Trash2 } from "lucide-react";
import { apiErrorMessage, tasksApi } from "../../lib/api";
import type { Assistant, GeneratedTask, Provider, TaskTemplate } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Select, Spinner, Textarea } from "../../components/ui";
import { modelOptions } from "./PromptsTab";

export default function TasksTab({ assistant, providers }: { assistant: Assistant; providers: Provider[] }) {
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [tasks, setTasks] = useState<GeneratedTask[] | null>(null);
  const [error, setError] = useState("");
  const [templateOpen, setTemplateOpen] = useState(false);
  const [generateOpen, setGenerateOpen] = useState(false);

  const reload = async () => {
    try {
      const [tpl, tsk] = await Promise.all([tasksApi.templates(assistant.id), tasksApi.list(assistant.id)]);
      setTemplates(tpl);
      setTasks(tsk);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    void reload();
  }, [assistant.id]);

  return (
    <div className="space-y-5">
      <div className="flex gap-2">
        <Button variant="accent" onClick={() => setGenerateOpen(true)}>
          <Sparkles className="h-4 w-4" /> Сгенерировать задания
        </Button>
        <Button variant="secondary" onClick={() => setTemplateOpen(true)}>
          <Plus className="h-4 w-4" /> Шаблон типового задания
        </Button>
      </div>

      {templates.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold">Шаблоны типовых заданий</h2>
          <div className="grid gap-2 sm:grid-cols-2">
            {templates.map((template) => (
              <Card key={template.id} className="p-3.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{template.name}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {template.topic || "без темы"} · {template.difficulty}
                    </p>
                  </div>
                  <button
                    className="p-1 text-muted-foreground hover:text-destructive shrink-0"
                    onClick={async () => {
                      await tasksApi.removeTemplate(assistant.id, template.id);
                      reload();
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      <ErrorNote message={error} />
      <h2 className="text-sm font-semibold">Банк сгенерированных заданий</h2>
      {tasks === null ? (
        <Spinner />
      ) : tasks.length === 0 ? (
        <EmptyState
          title="Заданий пока нет"
          hint="Создайте шаблон (или просто укажите тему) и сгенерируйте варианты — затем одобрите удачные"
        />
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} assistantId={assistant.id} onChanged={reload} />
          ))}
        </div>
      )}

      <TemplateModal open={templateOpen} onClose={() => setTemplateOpen(false)} assistant={assistant} onCreated={reload} />
      <GenerateTasksModal
        open={generateOpen}
        onClose={() => setGenerateOpen(false)}
        assistant={assistant}
        providers={providers}
        templates={templates}
        onCreated={reload}
      />
    </div>
  );
}

function TaskCard({ task, assistantId, onChanged }: { task: GeneratedTask; assistantId: string; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-2">
        <button className="flex items-center gap-2 min-w-0 text-left" onClick={() => setExpanded(!expanded)}>
          {expanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
          <span className="text-sm truncate">{task.statement.slice(0, 110)}...</span>
        </button>
        <div className="flex items-center gap-1.5 shrink-0">
          {task.approved ? <Badge tone="success">одобрено</Badge> : <Badge>черновик</Badge>}
          <Badge tone="info">{task.difficulty}</Badge>
          {!task.approved && (
            <Button
              variant="secondary"
              onClick={async () => {
                await tasksApi.update(assistantId, task.id, { approved: true });
                onChanged();
              }}
            >
              <CheckCircle2 className="h-3.5 w-3.5" /> Одобрить
            </Button>
          )}
          <Button
            variant="destructive"
            onClick={async () => {
              await tasksApi.remove(assistantId, task.id);
              onChanged();
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      {expanded && (
        <div className="mt-3 ml-6 space-y-3 text-sm">
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase mb-1">Условие</p>
            <p className="whitespace-pre-wrap">{task.statement}</p>
          </div>
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase mb-1">Эталонное решение</p>
            <p className="whitespace-pre-wrap text-muted-foreground">{task.reference_solution}</p>
          </div>
          {task.rubric.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase mb-1">
                Рубрика (макс. {task.max_score} б.)
              </p>
              <ul className="space-y-0.5">
                {task.rubric.map((r, i) => (
                  <li key={i} className="text-xs">
                    <span className="font-medium">{r.criterion_name}</span> — {r.max_score} б.
                    {r.description ? ` · ${r.description}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-xs text-muted-foreground">Сгенерировано: {task.model_used}</p>
        </div>
      )}
    </Card>
  );
}

function TemplateModal({
  open,
  onClose,
  assistant,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  assistant: Assistant;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [difficulty, setDifficulty] = useState("medium");
  const [instructions, setInstructions] = useState("");
  const [example, setExample] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      await tasksApi.createTemplate(assistant.id, { name, topic, difficulty, instructions, example });
      onCreated();
      onClose();
      setName("");
      setInstructions("");
      setExample("");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Шаблон типового задания" open={open} onClose={onClose} wide>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Название">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="напр. Расчёт pH буфера" />
          </Field>
          <Field label="Тема">
            <Input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Буферные растворы" />
          </Field>
          <Field label="Сложность">
            <Select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
              <option value="easy">лёгкая</option>
              <option value="medium">средняя</option>
              <option value="hard">сложная</option>
            </Select>
          </Field>
        </div>
        <Field
          label="Инструкции генерации"
          hint="Что варьировать, какие данные давать, чего избегать — это и есть «типовое задание»"
        >
          <Textarea
            rows={4}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="Задача на расчёт pH ацетатного буфера. Варьировать концентрации 0.01–1 М и соотношение кислота/соль. Требовать учёт ионной силы только на сложном уровне..."
          />
        </Field>
        <Field label="Пример задачи в нужном стиле (необязательно)">
          <Textarea rows={4} value={example} onChange={(e) => setExample(e.target.value)} />
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={!name.trim()}>
            Сохранить шаблон
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function GenerateTasksModal({
  open,
  onClose,
  assistant,
  providers,
  templates,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  assistant: Assistant;
  providers: Provider[];
  templates: TaskTemplate[];
  onCreated: () => void;
}) {
  const production = useMemo(() => modelOptions(providers, true), [providers]);
  const [templateId, setTemplateId] = useState("");
  const [modelId, setModelId] = useState("");
  const [topic, setTopic] = useState("");
  const [difficulty, setDifficulty] = useState("medium");
  const [count, setCount] = useState(3);
  const [instructions, setInstructions] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!modelId && production[0]) setModelId(production[0].id);
  }, [production, modelId]);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      await tasksApi.generate(assistant.id, {
        template_id: templateId || null,
        model_entry_id: modelId,
        topic,
        difficulty,
        count,
        instructions,
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Генерация заданий" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Шаблон (необязательно)">
          <Select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
            <option value="">— без шаблона, по теме —</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Модель-генератор">
          <Select value={modelId} onChange={(e) => setModelId(e.target.value)}>
            {production.length === 0 && <option value="">— подключите production-провайдера —</option>}
            {production.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Тема">
            <Input value={topic} onChange={(e) => setTopic(e.target.value)} />
          </Field>
          <Field label="Сложность">
            <Select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
              <option value="easy">лёгкая</option>
              <option value="medium">средняя</option>
              <option value="hard">сложная</option>
            </Select>
          </Field>
          <Field label="Сколько">
            <Input type="number" min={1} max={10} value={count} onChange={(e) => setCount(Number(e.target.value))} />
          </Field>
        </div>
        <Field label="Доп. инструкции">
          <Textarea rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={!modelId}>
            <Sparkles className="h-4 w-4" /> Сгенерировать
          </Button>
        </div>
      </div>
    </Modal>
  );
}
