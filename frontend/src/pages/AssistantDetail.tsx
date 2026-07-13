import { ArrowLeft, ArrowRight, FlaskConical, GraduationCap, User as UserIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Badge, Button, ErrorNote, Spinner } from "../components/ui";
import { apiErrorMessage, assistantsApi, providersApi } from "../lib/api";
import { useApp } from "../lib/context";
import type { Assistant, Provider } from "../lib/types";
import CoursesTab from "./assistant/CoursesTab";
import MaterialsTab from "./assistant/MaterialsTab";
import PipelinesTab from "./assistant/PipelinesTab";
import ProfileTab from "./assistant/ProfileTab";
import PromptsTab from "./assistant/PromptsTab";
import TasksTab from "./assistant/TasksTab";

const STEPS = [
  {
    key: "materials",
    label: "Материалы",
    hint: "Загрузите РПД, конспекты и задачники. Из них извлекутся темы, справочные данные и нотация курса — их не нужно вбивать вручную.",
  },
  {
    key: "assistant",
    label: "Ассистент",
    hint: "Профиль дисциплины, критерии оценивания и системные промпты для генерации задач, проверки работ и разбора со студентом.",
  },
  {
    key: "tasks",
    label: "Задания",
    hint: "Сгенерируйте задачи в нотации курса, проверьте их автопроверкой (независимый решатель, сверка данных) и одобрите лучшие.",
  },
  {
    key: "review",
    label: "Проверка",
    hint: "Проверьте ассистента в деле: разбор со студентом и пайплайн оценивания работ.",
  },
] as const;

type StepKey = (typeof STEPS)[number]["key"];

export default function AssistantDetail() {
  const { id } = useParams<{ id: string }>();
  const [params, setParams] = useSearchParams();
  const { setSelectedId, reloadDisciplines } = useApp();
  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState("");

  const rawTab = params.get("tab") ?? "materials";
  // Совместимость со старыми ссылками (?tab=profile|prompts|pipeline|courses)
  const tab: string = rawTab === "profile" || rawTab === "prompts" ? "assistant" : rawTab === "pipeline" ? "review" : rawTab;

  const setTab = (key: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", key);
    setParams(next, { replace: true });
  };

  const reload = async () => {
    if (!id) return;
    try {
      setAssistant(await assistantsApi.get(id));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    if (id) setSelectedId(id);
    void reload();
    providersApi.list().then(setProviders).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (error) return <ErrorNote message={error} />;
  if (!assistant) return <Spinner />;

  return (
    <div className="max-w-5xl space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-semibold">{assistant.name}</h1>
            {assistant.discipline !== assistant.name && (
              <Badge tone="accent" className="shrink-0">
                {assistant.discipline}
              </Badge>
            )}
          </div>
          {assistant.created_by_name && (
            <p className="mt-1 flex items-center gap-1.5 flex-wrap text-[11px] text-muted-foreground">
              <UserIcon className="h-3 w-3" />
              создал {assistant.created_by_name}
              {assistant.updated_by_name && assistant.updated_at && (
                <span>· изменение {assistant.updated_by_name}, {new Date(assistant.updated_at).toLocaleString("ru-RU")}</span>
              )}
            </p>
          )}
        </div>
        <div className="flex w-full items-center gap-2 sm:w-auto sm:shrink-0">
          <Button variant="ghost" onClick={() => setTab("courses")} title="Привязка к курсам Picrete">
            <GraduationCap className="h-4 w-4" /> Курсы
          </Button>
          <Link
            to="/playground"
            className="inline-flex items-center justify-center gap-2 rounded-md bg-accent px-3.5 py-2 text-sm font-medium text-accent-foreground transition-colors hover:opacity-90"
          >
            <FlaskConical className="h-4 w-4" /> Playground
          </Link>
        </div>
      </div>

      {tab === "courses" ? (
        <div className="space-y-4">
          <button className="text-sm text-accent hover:underline" onClick={() => setTab("materials")}>
            ← вернуться к пайплайну
          </button>
          <CoursesTab assistant={assistant} />
        </div>
      ) : (
        <>
          <StepBanner activeKey={tab} />

          <div className="pt-1">
            {tab === "materials" && <MaterialsTab assistant={assistant} providers={providers} onProfileChanged={reload} />}
            {tab === "assistant" && (
              <div className="space-y-8">
                <ProfileTab
                  assistant={assistant}
                  onSaved={async () => {
                    await reload();
                    await reloadDisciplines();
                  }}
                />
                <div className="border-t border-border pt-6">
                  <PromptsTab assistant={assistant} providers={providers} />
                </div>
              </div>
            )}
            {tab === "tasks" && <TasksTab assistant={assistant} providers={providers} />}
            {tab === "review" && <PipelinesTab assistant={assistant} providers={providers} />}
          </div>

          <StepFooter activeKey={tab} onGo={setTab} />
        </>
      )}

    </div>
  );
}

function StepBanner({ activeKey }: { activeKey: string }) {
  const index = STEPS.findIndex((s) => s.key === activeKey);
  const step = STEPS[index] ?? STEPS[0];
  return (
    <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-4 shadow-soft">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-semibold text-accent-foreground">
        {index + 1}
      </span>
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Шаг {index + 1} из {STEPS.length}
          </span>
          <h2 className="text-sm font-semibold">{step.label}</h2>
        </div>
        <p className="mt-0.5 text-sm text-muted-foreground leading-relaxed">{step.hint}</p>
      </div>
    </div>
  );
}

function StepFooter({ activeKey, onGo }: { activeKey: string; onGo: (key: StepKey) => void }) {
  const index = STEPS.findIndex((s) => s.key === activeKey);
  const prev = index > 0 ? STEPS[index - 1] : null;
  const next = index >= 0 && index < STEPS.length - 1 ? STEPS[index + 1] : null;
  if (!prev && !next) return null;
  return (
    <div className="flex items-center justify-between gap-2 border-t border-border pt-4">
      {prev ? (
        <Button variant="ghost" onClick={() => onGo(prev.key)}>
          <ArrowLeft className="h-4 w-4" /> {prev.label}
        </Button>
      ) : (
        <span />
      )}
      {next && (
        <Button variant="secondary" onClick={() => onGo(next.key)}>
          Далее: {next.label} <ArrowRight className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}
