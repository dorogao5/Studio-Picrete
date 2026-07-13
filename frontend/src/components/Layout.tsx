import clsx from "clsx";
import {
  Bot,
  Check,
  ChevronsUpDown,
  FlaskConical,
  GraduationCap,
  KeyRound,
  LayoutGrid,
  ListChecks,
  LogOut,
  Menu,
  Plug,
  Plus,
  Library,
  Users,
  Workflow,
  X,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { NavLink, Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { apiErrorMessage, authApi } from "../lib/api";
import { useApp } from "../lib/context";
import CreateDisciplineModal from "./CreateDisciplineModal";
import { Button, ErrorNote, Field, Input, Modal, Spinner } from "./ui";

const DESKTOP_MEDIA_QUERY = "(min-width: 1024px)";

export default function Layout() {
  const token = localStorage.getItem("studio_token");
  const { me, loading, selected } = useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [pwOpen, setPwOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [isDesktop, setIsDesktop] = useState(() => window.matchMedia(DESKTOP_MEDIA_QUERY).matches);
  const navRef = useRef<HTMLElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const navTitleId = useId();
  const drawerOpen = navOpen && !isDesktop;
  const navHidden = !isDesktop && !drawerOpen;

  useEffect(() => {
    const mediaQuery = window.matchMedia(DESKTOP_MEDIA_QUERY);
    const syncViewport = () => {
      setIsDesktop(mediaQuery.matches);
      if (mediaQuery.matches) setNavOpen(false);
    };
    syncViewport();
    mediaQuery.addEventListener("change", syncViewport);
    return () => mediaQuery.removeEventListener("change", syncViewport);
  }, []);

  useEffect(() => {
    if (!drawerOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = window.requestAnimationFrame(() => {
      navRef.current?.querySelector<HTMLElement>("button:not([disabled]), a[href]")?.focus();
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setNavOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const controls = Array.from(
        navRef.current?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((element) => element.offsetParent !== null);
      if (controls.length === 0) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      if (!window.matchMedia(DESKTOP_MEDIA_QUERY).matches) menuButtonRef.current?.focus();
    };
  }, [drawerOpen]);

  if (!token) return <Navigate to="/login" replace />;
  if (loading) return <div className="min-h-screen flex items-center justify-center"><Spinner /></div>;

  const tabLink = (tab: string) => (selected ? `/disciplines/${selected.id}?tab=${tab}` : "#");
  const rawTab = new URLSearchParams(location.search).get("tab") ?? "materials";
  // Совместимость со старыми ссылками: профиль/промпты → «Ассистент», пайплайн → «Проверка».
  const currentTab =
    rawTab === "profile" || rawTab === "prompts" ? "assistant" : rawTab === "pipeline" ? "review" : rawTab;
  const onDiscipline = location.pathname.startsWith("/disciplines/") && selected;

  const scopedNav = [
    { tab: "materials", label: "1 · Материалы", icon: Library },
    { tab: "assistant", label: "2 · Ассистент", icon: Bot },
    { tab: "tasks", label: "3 · Задания", icon: ListChecks },
    { tab: "review", label: "4 · Проверка", icon: Workflow },
    { tab: "courses", label: "Курсы", icon: GraduationCap },
  ];

  return (
    <div className="min-h-screen lg:flex">
      {/* Мобильная шапка */}
      <header className="lg:hidden sticky top-0 z-40 flex items-center gap-3 border-b border-border bg-card px-3 py-2.5">
        <button
          ref={menuButtonRef}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-muted"
          aria-label="Открыть меню"
          aria-expanded={drawerOpen}
          aria-controls="studio-navigation"
          onClick={() => setNavOpen(true)}
        >
          <Menu className="h-5 w-5" />
        </button>
        <span className="font-semibold text-sm tracking-tight">Picrete Studio</span>
      </header>

      {drawerOpen && (
        <div aria-hidden="true" className="fixed inset-0 z-40 bg-foreground/40 lg:hidden" onClick={() => setNavOpen(false)} />
      )}
      <aside
        id="studio-navigation"
        ref={navRef}
        role={drawerOpen ? "dialog" : undefined}
        aria-modal={drawerOpen ? "true" : undefined}
        aria-labelledby={drawerOpen ? navTitleId : undefined}
        aria-hidden={navHidden || undefined}
        inert={navHidden || undefined}
        className={clsx(
          "w-60 shrink-0 border-r border-border bg-card flex flex-col",
          "fixed inset-y-0 left-0 z-50 transition-transform duration-200 lg:static lg:translate-x-0 lg:transition-none",
          drawerOpen ? "translate-x-0" : "-translate-x-full",
        )}
        onClick={(e) => {
          if ((e.target as HTMLElement).closest("a")) setNavOpen(false);
        }}
      >
        <div className="flex items-center justify-between px-4 py-3.5 border-b border-border">
          <div>
            <p id={navTitleId} className="font-semibold tracking-tight text-sm">Picrete Studio</p>
            <p className="text-[11px] text-muted-foreground">общий воркспейс преподавателей</p>
          </div>
          <button
            className="lg:hidden rounded-md p-1.5 text-muted-foreground hover:bg-muted"
            aria-label="Закрыть меню"
            onClick={() => setNavOpen(false)}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-2 border-b border-border">
          <DisciplineSwitcher onNavigate={(id) => navigate(`/disciplines/${id}?tab=materials`)} />
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

      <main className="flex-1 min-w-0 p-4 sm:p-6 lg:p-8 overflow-x-hidden">
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
      end={to === "/disciplines"}
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
