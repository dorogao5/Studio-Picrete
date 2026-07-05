import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { assistantsApi, authApi } from "./api";
import type { Assistant, UserOut } from "./types";

const SELECTED_KEY = "studio_discipline";

interface AppState {
  me: UserOut | null;
  disciplines: Assistant[];
  loading: boolean;
  reloadDisciplines: () => Promise<Assistant[]>;
  selectedId: string | null;
  setSelectedId: (id: string | null) => void;
  selected: Assistant | null;
}

const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used within AppProvider");
  return ctx;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<UserOut | null>(null);
  const [disciplines, setDisciplines] = useState<Assistant[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedIdState] = useState<string | null>(() => localStorage.getItem(SELECTED_KEY));

  const setSelectedId = useCallback((id: string | null) => {
    if (id) localStorage.setItem(SELECTED_KEY, id);
    else localStorage.removeItem(SELECTED_KEY);
    setSelectedIdState(id);
  }, []);

  const reloadDisciplines = useCallback(async () => {
    const list = await assistantsApi.list();
    setDisciplines(list);
    setSelectedIdState((prev) => (prev && list.some((d) => d.id === prev) ? prev : (list[0]?.id ?? null)));
    return list;
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [user, list] = await Promise.all([authApi.me(), assistantsApi.list()]);
        if (!alive) return;
        setMe(user);
        setDisciplines(list);
        setSelectedIdState((prev) => (prev && list.some((d) => d.id === prev) ? prev : (list[0]?.id ?? null)));
      } catch {
        /* interceptor redirects to /login on 401 */
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const selected = useMemo(
    () => disciplines.find((d) => d.id === selectedId) ?? null,
    [disciplines, selectedId],
  );

  return (
    <AppContext.Provider
      value={{ me, disciplines, loading, reloadDisciplines, selectedId, setSelectedId, selected }}
    >
      {children}
    </AppContext.Provider>
  );
}
