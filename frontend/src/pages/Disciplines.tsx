import { Plus, Trash2, User as UserIcon } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import CreateDisciplineModal from "../components/CreateDisciplineModal";
import { Badge, Button, Card, EmptyState, ErrorNote, Spinner } from "../components/ui";
import { apiErrorMessage, assistantsApi } from "../lib/api";
import { useApp } from "../lib/context";

export default function Disciplines() {
  const { disciplines, loading, reloadDisciplines, setSelectedId } = useApp();
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const removeDiscipline = async (id: string, name: string) => {
    if (!confirm(`Удалить дисциплину «${name}» со всеми материалами, промптами и задачами? Это действие необратимо.`)) {
      return;
    }
    setError("");
    setDeletingId(id);
    try {
      await assistantsApi.remove(id);
      await reloadDisciplines();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="max-w-5xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Дисциплины</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Общий воркспейс: все преподаватели видят и правят одни и те же дисциплины
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Создать
        </Button>
      </div>

      <ErrorNote message={error} />
      {loading ? (
        <Spinner />
      ) : disciplines.length === 0 ? (
        <EmptyState
          title="Дисциплин пока нет"
          hint="Создайте первую — например «Неорганическая химия, 1 курс». Она сразу станет доступна всем преподавателям."
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {disciplines.map((d) => (
            <Card
              key={d.id}
              className="relative p-5 h-full hover:shadow-md transition-shadow cursor-pointer"
              onClick={() => {
                setSelectedId(d.id);
                navigate(`/disciplines/${d.id}?tab=profile`);
              }}
            >
              <button
                className="absolute right-3 top-3 rounded p-1.5 text-muted-foreground/60 hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                title="Удалить дисциплину (только автор или администратор)"
                disabled={deletingId === d.id}
                onClick={(e) => {
                  e.stopPropagation();
                  void removeDiscipline(d.id, d.name);
                }}
              >
                <Trash2 className="h-4 w-4" />
              </button>
              <h2 className="font-semibold pr-8">{d.name}</h2>
              <Badge tone="accent" className="mt-2">
                {d.discipline}
              </Badge>
              <p className="text-sm text-muted-foreground mt-2 line-clamp-2">{d.description || "Без описания"}</p>
              <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                <span>{d.criteria.length} критериев</span>
                <span>{d.nuances.length} нюансов</span>
                <span>{d.topics.length} тем</span>
              </div>
              {d.created_by_name && (
                <div className="mt-2 flex items-center gap-1 text-[11px] text-muted-foreground">
                  <UserIcon className="h-3 w-3" /> {d.created_by_name}
                  {d.updated_by_name && d.updated_by_name !== d.created_by_name && (
                    <span>· правил {d.updated_by_name}</span>
                  )}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      <CreateDisciplineModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={async (created) => {
          await reloadDisciplines();
          setSelectedId(created.id);
          navigate(`/disciplines/${created.id}?tab=profile`);
        }}
      />
    </div>
  );
}
