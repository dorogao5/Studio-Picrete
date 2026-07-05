import { FlaskConical, User as UserIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, Button, ErrorNote, Spinner, Tabs } from "../components/ui";
import { apiErrorMessage, assistantsApi, providersApi } from "../lib/api";
import { useApp } from "../lib/context";
import type { Assistant, Provider } from "../lib/types";
import CoursesTab from "./assistant/CoursesTab";
import MaterialsTab from "./assistant/MaterialsTab";
import PipelinesTab from "./assistant/PipelinesTab";
import ProfileTab from "./assistant/ProfileTab";
import PromptsTab from "./assistant/PromptsTab";
import TasksTab from "./assistant/TasksTab";

const TABS = [
  { key: "profile", label: "Профиль и критерии" },
  { key: "materials", label: "Материалы курса" },
  { key: "prompts", label: "Промпты" },
  { key: "tasks", label: "Задания" },
  { key: "pipeline", label: "Пайплайн проверки" },
  { key: "courses", label: "Курсы" },
];

export default function AssistantDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { setSelectedId, reloadDisciplines } = useApp();
  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState("");

  const tab = params.get("tab") ?? "profile";
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
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold truncate">{assistant.name}</h1>
            <Badge tone="accent">{assistant.discipline}</Badge>
          </div>
          {assistant.created_by_name && (
            <p className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <UserIcon className="h-3 w-3" />
              создал {assistant.created_by_name}
              {assistant.updated_by_name && assistant.updated_at && (
                <span>· последнее изменение {assistant.updated_by_name}, {new Date(assistant.updated_at).toLocaleString("ru-RU")}</span>
              )}
            </p>
          )}
        </div>
        <Link to="/playground">
          <Button variant="accent">
            <FlaskConical className="h-4 w-4" /> Playground
          </Button>
        </Link>
      </div>

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "profile" && (
        <ProfileTab
          assistant={assistant}
          onSaved={async () => {
            await reload();
            await reloadDisciplines();
          }}
        />
      )}
      {tab === "materials" && <MaterialsTab assistant={assistant} providers={providers} onProfileChanged={reload} />}
      {tab === "prompts" && <PromptsTab assistant={assistant} providers={providers} />}
      {tab === "tasks" && <TasksTab assistant={assistant} providers={providers} />}
      {tab === "pipeline" && <PipelinesTab assistant={assistant} providers={providers} />}
      {tab === "courses" && <CoursesTab assistant={assistant} />}

      <div className="pt-6">
        <Button
          variant="destructive"
          onClick={async () => {
            if (confirm(`Удалить дисциплину «${assistant.name}» со всеми промптами, задачами и пайплайнами?`)) {
              await assistantsApi.remove(assistant.id);
              await reloadDisciplines();
              navigate("/disciplines");
            }
          }}
        >
          Удалить дисциплину
        </Button>
      </div>
    </div>
  );
}
