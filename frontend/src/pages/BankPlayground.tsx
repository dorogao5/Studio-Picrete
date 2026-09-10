import BankImage from "../components/BankImage";
import { MathTaskSelect } from "../components/MathTaskSelect";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiErrorMessage, coursesApi, playgroundApi } from "../lib/api";
import type { Assistant, Course } from "../lib/types";
import { Badge, Button, Card, ErrorNote, Field, Input, Select, Textarea } from "../components/ui";
import MathText from "../components/MathText";

type BankTask = { images: string[]; id: string; number: string; topic: string; text: string; solution: string; difficulty: string | null };
type Preview = { result_id: string; snapshot_version: string; draft: boolean; task_number: string; output: {
  total_score: number; max_score: number; feedback: string; needs_teacher_review?: boolean; unreadable?: boolean;
  detailed_analysis?: { errors_found?: string[] };
  criteria_scores: { criterion_name: string; score: number; max_score: number; comment: string }[];
  _metadata: { model: string; grader_prompt_version: number; duration_seconds: number };
} };

export default function BankPlayground({ assistant }: { assistant: Assistant }) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseId, setCourseId] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [skip, setSkip] = useState(0);
  const [items, setItems] = useState<BankTask[]>([]);
  const [total, setTotal] = useState(0);
  const [task, setTask] = useState<BankTask | null>(null);
  const [text, setText] = useState("");
  const [mode, setMode] = useState("draft");
  const [result, setResult] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [comment, setComment] = useState("");
  const [saved, setSaved] = useState(false);
  const [cases, setCases] = useState<{ id: string; taskId: string; number: string; answer: string }[]>([]);
  const base = `/assistants/${assistant.id}/courses/${courseId}`;

  useEffect(() => {
    let active = true;
    coursesApi.list(assistant.id).then((all) => {
      if (!active) return;
      const bound = all.filter((c) => c.external_course_id);
      setCourses(bound); setCourseId(bound[0]?.id ?? "");
    }).catch((e) => { if (active) setError(apiErrorMessage(e)); });
    return () => { active = false; };
  }, [assistant.id]);

  useEffect(() => {
    if (!courseId) return;
    let active = true;
    setLoading(true); setItems([]); setError("");
    api.get<{ items: BankTask[]; total: number }>(`${base}/task-bank`, { params: { q: query, skip } }).then(({ data }) => {
      if (active) { setItems(data.items); setTotal(data.total); }
    }).catch((e) => { if (active) setError(apiErrorMessage(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [base, courseId, query, skip]);

  useEffect(() => {
    let active = true;
    playgroundApi.runs(assistant.id).then((runs) => {
      if (!active) return;
      const externalId = courses.find((c) => c.id === courseId)?.external_course_id;
      setCases(runs.flatMap((run) => {
        const info = (run.results[0]?.output as unknown as { _studio?: { task_id: string; task_number: string; course_id: string } } | null)?._studio;
        return info && info.course_id === externalId ? [{ id: run.id, taskId: info.task_id, number: info.task_number, answer: run.ocr_text }] : [];
      }));
    }).catch(() => {});
    return () => { active = false; };
  }, [assistant.id, courseId, courses, result]);

  const grade = async () => {
    if (!task || busy) return;
    setBusy(true); setError(""); setResult(null); setSaved(false);
    try { setResult((await api.post<Preview>(`${base}/grading-preview`, { task_id: task.id, student_text: text, mode })).data); }
    catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  };
  const review = async (rating: number) => {
    if (!result) return;
    try { await playgroundApi.feedback(result.result_id, { rating, comment }); setSaved(true); }
    catch (e) { setError(apiErrorMessage(e)); }
  };

  return <div className="space-y-5">
    <Card className="p-5 space-y-3">
      <h2 className="font-semibold">Свиридов → проверка → публикация в курс</h2>
      <p className="text-sm text-muted-foreground">Выберите задачу с эталонным решением, напишите ответ от лица студента и оцените проверку. Здесь работает движок проверки основного Picrete. Черновик использует текущий профиль и активный промпт проверки; опубликованная версия — настройки, доступные в курсе.</p>
      <Field label="Курс Picrete"><Select value={courseId} disabled={busy} onChange={(e) => { setCourseId(e.target.value); setSkip(0); setTask(null); setResult(null); }}>
        {!courses.length && <option value="">Нет привязанных курсов — откройте «Курсы» ассистента</option>}
        {courses.map((c) => <option value={c.id} key={c.id}>{c.name}</option>)}
      </Select></Field>
      <p className="text-xs text-muted-foreground">Фото можно проверить в «Проверке работы». Текстовый прогон проверяет оценивание после распознавания; качество OCR проверяйте отдельно на фото.</p>
    </Card>
    <ErrorNote message={error} />
    {courseId && <div className="grid gap-5 lg:grid-cols-2">
      <Card className="p-5 space-y-4">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setSkip(0); setQuery(q.trim()); }}>
          <Input aria-label="Поиск задачи Свиридова" placeholder="Номер (например, 7.61) или текст" value={q} onChange={(e) => setQ(e.target.value)} />
          <Button type="submit" disabled={loading}>Найти</Button>
        </form>
        <p className="text-xs text-muted-foreground">Только с полным эталоном · найдено {total}</p>
        <div className="max-h-80 overflow-y-auto space-y-2" aria-busy={loading}>
          {loading ? <p>Загрузка банка…</p> : items.length === 0 ? <p>Задачи не найдены. Измените запрос.</p> : items.map((item) => <button type="button" key={item.id} disabled={busy} aria-pressed={task?.id === item.id} className={`w-full rounded-lg border p-3 text-left ${task?.id === item.id ? "border-accent bg-accent/10" : "border-border"}`} onClick={() => { setTask(item); setResult(null); setText(""); setSaved(false); }}>
            <span className="font-semibold">№ {item.number}</span><span className="ml-2 text-xs text-muted-foreground"><MathText inline>{item.topic}</MathText> · {item.difficulty}</span>
            <MathText className="mt-1 text-sm line-clamp-2">{item.text}</MathText>
          </button>)}
        </div>
        <div className="flex justify-between"><Button variant="secondary" disabled={skip === 0 || loading} onClick={() => setSkip(Math.max(0, skip - 25))}>Назад</Button><Button variant="secondary" disabled={skip + 25 >= total || loading} onClick={() => setSkip(skip + 25)}>Далее</Button></div>
        {task && <section className="space-y-3 border-t border-border pt-4">
          <h3 className="font-semibold">Задача № {task.number}</h3><MathText>{task.text}</MathText>
          {task.images?.map((id) => <BankImage key={id} url={`${base}/task-bank/${task.id}/images/${id}`} />)}
          {!!task.images?.length && <p className="text-xs text-warning">В задаче есть рисунок: текстовая модель его не видит. Если он нужен для решения, проверьте вывод вручную.</p>}
          <details><summary className="cursor-pointer text-sm font-medium">Эталонное решение (для преподавателя)</summary><MathText className="mt-3 text-sm">{task.solution}</MathText></details>
        </section>}
      </Card>
      <Card className="p-5 space-y-4">
        <Field label="Версия настроек"><Select value={mode} disabled={busy} onChange={(e) => { setMode(e.target.value); setResult(null); }}><option value="draft">Черновик — текущие настройки студии</option><option value="published">Опубликованная — основной Picrete</option></Select></Field>
        <Field label="Ответ студента текстом" hint="Проверьте полный верный ответ, частичный ответ и типичную ошибку. Формулы можно писать обычным текстом или LaTeX."><Textarea rows={10} value={text} disabled={busy} onChange={(e) => setText(e.target.value)} placeholder="Напишите ход решения…" /></Field>
        <Button onClick={grade} loading={busy} disabled={!task || !text.trim()}>Проверить ответ</Button>
        {cases.length > 0 && <Field label="Повторить сохранённый ответ"><MathTaskSelect value="" disabled={busy} onChange={id => {
          const previous = cases.find((c) => c.id === id); if (!previous) return;
          setTask(null); setQ(previous.number); setQuery(previous.number); setSkip(0); setText(previous.answer); setResult(null);
          api.get<{items: BankTask[]}>(`${base}/task-bank`, { params: { q: previous.number } }).then(({data}) => {
            setTask(data.items.find((t) => t.id === previous.taskId) ?? null);
          }).catch((err) => setError(apiErrorMessage(err)));
        }} placeholder="Выберите предыдущий прогон" options={cases.map(c => ({ id: c.id, text: `№ ${c.number} · ${c.answer}` }))} /></Field>}
        {result && <section className="space-y-3 border-t border-border pt-4">
          <div className="flex flex-wrap gap-2"><Badge>{result.draft ? "Черновик" : "Опубликовано"}</Badge><Badge>{result.output.total_score} / {result.output.max_score}</Badge></div>
          {result.output.needs_teacher_review && <p className="text-sm text-warning">Требуется проверка преподавателя</p>}
          <MathText>{result.output.feedback}</MathText>
          {!!result.output.detailed_analysis?.errors_found?.length && <div className="rounded-lg bg-muted p-3 text-sm"><strong>Замечания проверки</strong>{result.output.detailed_analysis.errors_found.map((issue, i) => <MathText key={i}>{issue}</MathText>)}</div>}
          {result.output.criteria_scores?.map((c, i) => <div key={i} className="text-sm"><strong><MathText inline>{c.criterion_name}</MathText>: {c.score}/{c.max_score}</strong><MathText>{c.comment}</MathText></div>)}
          <p className="text-xs text-muted-foreground break-all">{result.output._metadata.model} · промпт v{result.output._metadata.grader_prompt_version} · версия {result.snapshot_version.slice(0, 12)} · № {result.task_number}</p>
          <Field label="Комментарий преподавателя"><Textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Что проверено верно, какую ошибку нужно исправить?" /></Field>
          <div className="flex flex-wrap gap-2"><Button variant="secondary" onClick={() => review(5)}>Проверка корректна</Button><Button variant="secondary" onClick={() => review(1)}>Нужна доработка</Button></div>
          {saved && <p className="text-xs text-success">Оценка сохранена в истории.</p>}
        </section>}
      </Card>
    </div>}
    <Card className="p-5 space-y-2 text-sm"><p>После ошибки уточните «Нюансы» профиля или создайте и активируйте новую версию промпта «Проверка решений». Повторите сохранённые ответы и отметьте корректную проверку.</p><p>Затем откройте «Курсы» → «Опубликовать обновление». Публикация переносит профиль, активные промпты и справочники. Новые задачи из банка получают ту же шкалу; уже созданные работы сохраняют свои критерии.</p><Link className="text-accent underline" to={`/disciplines/${assistant.id}?tab=assistant`}>Открыть настройки и публикацию ассистента →</Link></Card>
  </div>;
}
