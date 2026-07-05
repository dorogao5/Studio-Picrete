import clsx from "clsx";
import {
  Bot,
  Check,
  ChevronsUpDown,
  FileText,
  FlaskConical,
  GraduationCap,
  KeyRound,
  LayoutGrid,
  ListChecks,
  LogOut,
  Plug,
  Plus,
  Library,
  Users,
  Workflow,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { apiErrorMessage, authApi } from "../lib/api";
import { useApp } from "../lib/context";
import CreateDisciplineModal from "./CreateDisciplineModal";
import { Button, ErrorNote, Field, Input, Modal, Spinner } from "./ui";

export default function Layout() {
  const token = localStorage.getItem("studio_token");
  const { me, loading, selected } = useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [pwOpen, setPwOpen] = useState(false);

  if (!token) return <Navigate to="/login" replace />;
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;

  const tabLink = (tab: string) => (selected ? `/disciplines/${selected.id}?tab=${tab}` : "#");
  const currentTab = new URLSearchParams(location.search).get("tab") ?? "profile";
  const onDiscipline = location.pathname.startsWith("/disciplines/") && selected;

  const scopedNav = [
    { tab: "profile", label: "Профиль и критерии", icon: FileText },
    { tab: "materials", label: "Материалы курса", icon: Library },
    { tab: "prompts", label: "Промпты", icon: Bot },
    { tab: "tasks", label: "Задания", icon: ListChecks },
    { tab: "pipeline", label: "Пайплайн проверки", icon: Workflow },
    { tab: "courses", label: "Курсы", icon: GraduationCap },
  ];

  return (
    <div className="min-h-screen flex">
      <aside className="w-60 shrink-0 border-r border-border bg-card flex flex-col">
        <div className="px-4 py-3.5 border-b border-border">
          <p className="font-semibold tracking-tight text-sm">Picrete Studio</p>
          <p className="text-[11px] text-muted-foreground">общий воркспейс преподавателей</p>
        </div>

        <div className="p-2 border-b border-border">
          <DisciplineSwitcher onNavigate={(id) => navigate(`/disciplines/${id}?tab=profile`)} />
        </div>

        <nav className="flex-1 overflow-y-auto p-2 space-y-4">
          <div className="space-y-0.5">
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Дисциплина
            </p>
            {selected ? (
              <>
                {scopedNav.map(({ tab, label, icon: Icon }) => (
                  <NavLink
                    key={tab}
                    to={tabLink(tab)}
                    className={clsx(
                      "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                      onDiscipline && currentTab === tab
                        ? "bg-accent/10 text-accent"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {label}
                  </NavLink>
                ))}
                <NavLink
                  to="/playground"
                  className={({ isActive }) =>
                    clsx(
                      "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                      isActive ? "bg-accent/10 text-accent" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )
                  }
                >
                  <FlaskConical className="h-4 w-4" />
                  Playground
                </NavLink>
              </>
            ) : (
              <p className="px-3 py-2 text-xs text-muted-foreground">
                Выберите дисциплину сверху или создайте новую
              </p>
            )}
          </div>

          <div className="space-y-0.5">
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Общее
            </p>
            <SideLink to="/disciplines" icon={LayoutGrid} label="Все дисциплины" />
            <SideLink to="/providers" icon={Plug} label="Провайдеры LLM" />
            {me?.role === "admin" && <SideLink to="/users" icon={Users} label="Доступы" />}
          </div>
        </nav>

        <div className="border-t border-border p-2 space-y-0.5">
          <div className="px-3 py-1.5">
            <p className="text-sm font-medium truncate">{me?.full_name || me?.username}</p>
            <p className="text-[11px] text-muted-foreground">
              {me?.username} · {me?.role === "admin" ? "администратор" : "преподаватель"}
            </p>
          </div>
          <button
            onClick={() => setPwOpen(true)}
            className="w-full flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <KeyRound className="h-4 w-4" /> Сменить пароль
          </button>
          <button
            onClick={() => {
              localStorage.removeItem("studio_token");
              window.location.href = "/login";
            }}
            className="w-full flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <LogOut className="h-4 w-4" /> Выйти
          </button>
        </div>
      </aside>

      <main className="flex-1 min-w-0 p-6 lg:p-8 overflow-x-hidden">
        <Outlet />
      </main>

      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
    </div>
  );
}

function SideLink({ to, icon: Icon, label }: { to: string; icon: typeof Plug; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        clsx(
          "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
          isActive ? "bg-accent/10 text-accent" : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )
      }
    >
      <Icon className="h-4 w-4" />
      {label}
    </NavLink>
  );
}

function DisciplineSwitcher({ onNavigate }: { onNavigate: (id: string) => void }) {
  const { disciplines, selected, setSelectedId, reloadDisciplines } = useApp();
  const [open, setOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 rounded-md border border-border bg-background px-3 py-2 text-left hover:bg-muted"
      >
        <span className="min-w-0">
          {selected ? (
            <>
              <span className="block text-sm font-semibold truncate">{selected.name}</span>
              <span className="block text-[11px] text-muted-foreground truncate">{selected.discipline}</span>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">Выберите дисциплину</span>
          )}
        </span>
        <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-80 overflow-y-auto rounded-md border border-border bg-card shadow-soft">
            {disciplines.length === 0 && (
              <p className="px-3 py-3 text-xs text-muted-foreground">Пока нет дисциплин</p>
            )}
            {disciplines.map((d) => (
              <button
                key={d.id}
                onClick={() => {
                  setSelectedId(d.id);
                  setOpen(false);
                  onNavigate(d.id);
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-muted"
              >
                <Check className={clsx("h-4 w-4 shrink-0", selected?.id === d.id ? "text-accent" : "opacity-0")} />
                <span className="min-w-0">
                  <span className="block text-sm truncate">{d.name}</span>
                  <span className="block text-[11px] text-muted-foreground truncate">{d.discipline}</span>
                </span>
              </button>
            ))}
            <button
              onClick={() => {
                setOpen(false);
                setCreateOpen(true);
              }}
              className="w-full flex items-center gap-2 border-t border-border px-3 py-2 text-left text-sm text-accent hover:bg-muted"
            >
              <Plus className="h-4 w-4" /> Новая дисциплина
            </button>
          </div>
        </>
      )}

      <CreateDisciplineModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={async (created) => {
          await reloadDisciplines();
          setSelectedId(created.id);
          onNavigate(created.id);
        }}
      />
    </div>
  );
}

function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      await authApi.changePassword(current, next);
      setDone(true);
      setCurrent("");
      setNext("");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Смена пароля" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Текущий пароль">
          <Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <Field label="Новый пароль" hint="Минимум 6 символов">
          <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <ErrorNote message={error} />
        {done && <p className="text-sm text-success">Пароль изменён</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Закрыть
          </Button>
          <Button onClick={submit} loading={loading} disabled={!current || next.length < 6}>
            Сменить
          </Button>
        </div>
      </div>
    </Modal>
  );
}
