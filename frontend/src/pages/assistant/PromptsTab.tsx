import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Plus, Sparkles, Trash2 } from "lucide-react";
import { apiErrorMessage, promptsApi } from "../../lib/api";
import type { Assistant, PromptVersion, Provider } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Modal, Select, Spinner, Textarea } from "../../components/ui";

export function modelOptions(providers: Provider[], productionOnly: boolean) {
  return providers
    .filter((p) => p.enabled && (!productionOnly || p.purpose === "production"))
    .flatMap((p) =>
      p.models.filter((m) => m.enabled).map((m) => ({
        id: m.id,
        label: `${p.name} · ${m.display_name || m.model_id}`,
        family: m.family,
        vision: m.supports_vision,
      })),
    );
}

const ROLE_LABELS: Record<string, string> = { grader: "Проверка решений", generator: "Генерация заданий" };

export default function PromptsTab({ assistant, providers }: { assistant: Assistant; providers: Provider[] }) {
  const [prompts, setPrompts] = useState<PromptVersion[] | null>(null);
  const [error, setError] = useState("");
  const [generateOpen, setGenerateOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);

  const reload = async () => {
    try {
      setPrompts(await promptsApi.list(assistant.id));
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
          <Sparkles className="h-4 w-4" /> Сгенерировать ИИ-архитектором
        </Button>
        <Button variant="secondary" onClick={() => setManualOpen(true)}>
          <Plus className="h-4 w-4" /> Написать вручную
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Архитектор (например GPT-5.5) собирает системный промпт из профиля дисциплины, критериев и нюансов —
        с учётом рекомендаций для конкретного семейства целевой модели (DeepSeek, Qwen, YandexGPT). Активная версия
        используется в Playground и пайплайнах.
      </p>

      <ErrorNote message={error} />
      {prompts === null ? (
        <Spinner />
      ) : prompts.length === 0 ? (
        <EmptyState
          title="Промптов пока нет"
          hint="Заполните профиль и нажмите «Сгенерировать ИИ-архитектором» — это отправная точка для итераций"
        />
      ) : (
        (["grader", "generator"] as const).map((role) => {
          const rolePrompts = prompts.filter((p) => p.role === role);
          if (rolePrompts.length === 0) return null;
          return (
            <div key={role} className="space-y-2">
              <h2 className="text-sm font-semibold">{ROLE_LABELS[role]}</h2>
              {rolePrompts.map((prompt) => (
                <PromptCard key={prompt.id} prompt={prompt} assistantId={assistant.id} onChanged={reload} />
              ))}
            </div>
          );
        })
      )}

      <GenerateModal
        open={generateOpen}
        onClose={() => setGenerateOpen(false)}
        assistant={assistant}
        providers={providers}
        onCreated={reload}
      />
      <ManualModal open={manualOpen} onClose={() => setManualOpen(false)} assistant={assistant} onCreated={reload} />
    </div>
  );
}

function PromptCard({ prompt, assistantId, onChanged }: { prompt: PromptVersion; assistantId: string; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-2">
        <button className="flex items-center gap-2 min-w-0 text-left" onClick={() => setExpanded(!expanded)}>
          {expanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
          <span className="font-medium text-sm">v{prompt.version}</span>
          {prompt.status === "active" && <Badge tone="success">активен</Badge>}
          {prompt.status === "draft" && <Badge>черновик</Badge>}
          {prompt.status === "archived" && <Badge>архив</Badge>}
          <Badge tone="info">{prompt.target_family}</Badge>
          {prompt.source === "generated" && <Badge tone="accent">архитектор: {prompt.architect_model}</Badge>}
        </button>
        <div className="flex gap-1.5 shrink-0">
          {prompt.status !== "active" && (
            <Button
              variant="secondary"
              onClick={async () => {
                await promptsApi.activate(assistantId, prompt.id);
                onChanged();
              }}
            >
              <CheckCircle2 className="h-3.5 w-3.5" /> Активировать
            </Button>
          )}
          <Button
            variant="destructive"
            onClick={async () => {
              if (confirm(`Удалить версию v${prompt.version}?`)) {
                await promptsApi.remove(assistantId, prompt.id);
                onChanged();
              }
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      {prompt.notes && <p className="text-xs text-muted-foreground mt-2 ml-6">{prompt.notes}</p>}
      {expanded && (
        <pre className="mt-3 ml-6 whitespace-pre-wrap rounded-md bg-muted p-3 text-xs font-mono max-h-96 overflow-y-auto">
          {prompt.system_prompt}
        </pre>
      )}
    </Card>
  );
}

function GenerateModal({
  open,
  onClose,
  assistant,
  providers,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  assistant: Assistant;
  providers: Provider[];
  onCreated: () => void;
}) {
  const production = useMemo(() => modelOptions(providers, true), [providers]);

  const [role, setRole] = useState("grader");
  const [targetId, setTargetId] = useState("");
  const [extra, setExtra] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!targetId && production[0]) setTargetId(production[0].id);
  }, [production, targetId]);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      await promptsApi.generate(assistant.id, {
        role,
        target_model_entry_id: targetId,
        extra_instructions: extra,
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
    <Modal title="Генерация системного промпта" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Назначение промпта">
          <Select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="grader">Проверка решений</option>
            <option value="generator">Генерация заданий</option>
          </Select>
        </Field>
        <Field
          label="Целевая модель (кто будет работать по промпту)"
          hint="Промпт адаптируется под особенности семейства: DeepSeek V4, Qwen 3.x, YandexGPT, Alice AI"
        >
          <Select value={targetId} onChange={(e) => setTargetId(e.target.value)}>
            {production.length === 0 && <option value="">— подключите провайдера —</option>}
            {production.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Дополнительные пожелания (необязательно)">
          <Textarea
            rows={3}
            value={extra}
            onChange={(e) => setExtra(e.target.value)}
            placeholder="напр. строже к оформлению размерностей; фидбек — доброжелательный, на «вы»"
          />
        </Field>
        <p className="text-xs text-muted-foreground">
          Промпт пишет фоновая модель-архитектор по best-practices и рекомендациям для выбранного семейства — вам её
          настраивать не нужно.
        </p>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={!targetId}>
            <Sparkles className="h-4 w-4" /> Сгенерировать
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ManualModal({
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
  const [role, setRole] = useState("grader");
  const [text, setText] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      await promptsApi.create(assistant.id, { role, system_prompt: text, notes });
      onCreated();
      onClose();
      setText("");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Новая версия промпта вручную" open={open} onClose={onClose} wide>
      <div className="space-y-4">
        <Field label="Назначение">
          <Select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="grader">Проверка решений</option>
            <option value="generator">Генерация заданий</option>
          </Select>
        </Field>
        <Field label="Системный промпт">
          <Textarea rows={14} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        <Field label="Заметка к версии">
          <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="что изменили и зачем" />
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={!text.trim()}>
            Сохранить
          </Button>
        </div>
      </div>
    </Modal>
  );
}
