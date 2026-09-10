import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Check, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { api, apiErrorMessage, coursesApi } from "../lib/api";
import type { BankTask, BankSelection, Course } from "../lib/types";
import { Badge, Button, ErrorNote, Field, Input, Select } from "./ui";
import MathText from "./MathText";
import BankImage from "./BankImage";

export type { BankSelection } from "../lib/types";

export default function CourseBankPicker({ assistantId, value, onChange, disabled = false }: {
  assistantId: string; value: BankSelection | null; onChange: (value: BankSelection | null) => void; disabled?: boolean;
}) {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [courseId, setCourseId] = useState(value?.courseId ?? "");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [skip, setSkip] = useState(0);
  const [items, setItems] = useState<BankTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [browsing, setBrowsing] = useState(!value);
  const [retry, setRetry] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const base = `/assistants/${assistantId}/courses/${courseId}`;
  useEffect(() => {
    let active = true;
    coursesApi.list(assistantId).then(list => {
      if (!active) return;
      const bound = list.filter(course => course.external_course_id);
      setCourses(bound); setCourseId(current => bound.some(c => c.id === current) ? current : bound[0]?.id ?? "");
    }).catch(e => { if (active) { setError(apiErrorMessage(e)); setCourses([]); } });
    return () => { active = false; };
  }, [assistantId, retry]);
  useEffect(() => {
    if (value) { setCourseId(value.courseId); setBrowsing(false); }
  }, [value]);
  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(search.trim()); setSkip(0); }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    if (!courseId || !browsing) return;
    let active = true;
    setLoading(true); setError("");
    api.get<{items: BankTask[]; total: number}>(`${base}/task-bank`, { params: {q: query, skip} }).then(({data}) => {
      if (!active) return;
      setItems(data.items); setTotal(data.total); listRef.current?.scrollTo({top: 0});
    }).catch(e => { if (active) { setItems([]); setError(apiErrorMessage(e)); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [base, courseId, query, skip, browsing, retry]);
  return <div className="space-y-3">
    {error && <><ErrorNote message={error} /><Button variant="secondary" onClick={() => setRetry(n => n + 1)}>Повторить загрузку</Button></>}
    {courses === null ? <p className="text-sm text-muted-foreground">Загружаем курсы…</p> : !courses.length && !error ? <p className="text-sm text-muted-foreground">Чтобы выбирать задачи Свиридова, <Link className="text-accent underline" to={`/disciplines/${assistantId}?tab=courses`}>привяжите курс Picrete</Link>.</p> : null}
    {browsing && courseId && <>
      {courses && courses.length > 1 && <Field label="Курс"><Select value={courseId} disabled={disabled} onChange={e => { setCourseId(e.target.value); setSkip(0); onChange(null); }}>{courses.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>}
      <form className="flex gap-2" onSubmit={e => { e.preventDefault(); setQuery(search.trim()); setSkip(0); }}>
        <Input aria-label="Номер или текст задачи Свиридова" placeholder="Номер, например 7.61, или текст задачи" value={search} disabled={disabled} onChange={e => setSearch(e.target.value)} />
        <Button type="submit" variant="secondary" disabled={disabled || loading} aria-label="Найти задачу"><Search className="h-4 w-4" /></Button>
      </form>
      <p className="text-xs text-muted-foreground">Задачи Свиридова с полным эталонным решением</p>
      <div ref={listRef} className="max-h-72 overflow-y-auto space-y-2" aria-busy={loading}>
        {loading ? <p role="status" className="py-6 text-center text-sm text-muted-foreground">Загружаем задачи…</p> : !items.length && !error ? <p className="py-6 text-center text-sm text-muted-foreground">Ничего не найдено. Попробуйте другой номер или часть условия.</p> : items.map(task => <button key={task.id} type="button" disabled={disabled} aria-pressed={value?.task.id === task.id} onClick={() => { onChange({courseId, task}); setBrowsing(false); }} className="w-full rounded-lg border border-border p-3 text-left transition-colors hover:border-accent hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50">
          <span className="flex justify-between gap-2"><strong className="text-sm">№ {task.number}</strong><span className="text-xs text-accent">Выбрать</span></span>
          <MathText className="mt-1 line-clamp-2 text-sm">{task.text}</MathText>
          <MathText className="mt-1 text-xs text-muted-foreground">{task.topic}</MathText>
        </button>)}
      </div>
      <nav aria-label="Страницы банка задач" className="flex items-center justify-between gap-2 border-t border-border pt-3">
        <Button variant="ghost" aria-label="Предыдущая страница задач" disabled={disabled || loading || skip === 0} onClick={() => setSkip(n => Math.max(0, n - 25))}><ChevronLeft className="h-4 w-4" /></Button>
        <span role="status" className="text-xs text-muted-foreground">{loading ? "Загрузка…" : total ? `${skip + 1}–${Math.min(skip + 25, total)} из ${total} · стр. ${Math.floor(skip / 25) + 1}` : "0 задач"}</span>
        <Button variant="ghost" aria-label="Следующая страница задач" disabled={disabled || loading || skip + 25 >= total} onClick={() => setSkip(n => n + 25)}><ChevronRight className="h-4 w-4" /></Button>
      </nav>
      {value && <Button variant="ghost" onClick={() => setBrowsing(false)}>Вернуться к выбранной задаче</Button>}
    </>}
    {value && !browsing && <section className="rounded-lg border border-accent/30 bg-accent/5 p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><Badge tone="success"><Check className="h-3 w-3" /> Свиридов · № {value.task.number}</Badge><Button variant="ghost" disabled={disabled} onClick={() => setBrowsing(true)}>Сменить задачу</Button></div>
      <MathText className="text-sm">{value.task.text}</MathText>
      {value.task.images?.map(id => <BankImage key={id} url={`/assistants/${assistantId}/courses/${value.courseId}/task-bank/${value.task.id}/images/${id}`} />)}
      {!!value.task.images?.length && <p className="text-xs text-warning">Рисунок виден здесь, но текстовая модель его не получает. Если он нужен для решения, проверьте ответ вручную.</p>}
      <details className="text-sm"><summary className="cursor-pointer font-medium">Эталон для преподавателя</summary><MathText className="mt-3">{value.task.solution}</MathText></details>
    </section>}
  </div>;
}
