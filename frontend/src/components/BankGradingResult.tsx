import { useState } from "react";
import { playgroundApi, apiErrorMessage } from "../lib/api";
import { Badge, Button, Card, ErrorNote, Field, Textarea } from "./ui";
import MathText from "./MathText";
export type BankPreview = { result_id: string; snapshot_version: string; draft: boolean; task_number: string; output: {
  total_score: number; max_score: number; feedback: string; needs_teacher_review?: boolean; unreadable?: boolean;
  detailed_analysis?: { errors_found?: string[] };
  criteria_scores: { criterion_name: string; score: number; max_score: number; comment: string }[];
  _metadata: { model: string; grader_prompt_version: number; duration_seconds: number };
} };


export default function BankGradingResult({ result }: {result: BankPreview}) {
  const [comment, setComment] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const review = async (rating: number) => {
    setSaving(true); setError("");
    try { await playgroundApi.feedback(result.result_id, {rating, comment}); setSaved(true); }
    catch (e) { setError(apiErrorMessage(e)); }
    finally { setSaving(false); }
  };
  return <Card className="p-5 space-y-3" aria-label="Результат проверки">
    <h2 className="font-semibold">Результат проверки · задача курса № {result.task_number}</h2>
          <div className="flex flex-wrap gap-2"><Badge>{result.draft ? "Черновик" : "Опубликовано"}</Badge><Badge>{result.output.total_score} / {result.output.max_score}</Badge></div>
          {result.output.needs_teacher_review && <p className="text-sm text-warning">Требуется проверка преподавателя</p>}
          <MathText>{result.output.feedback}</MathText>
          {!!result.output.detailed_analysis?.errors_found?.length && <div className="rounded-lg bg-muted p-3 text-sm"><strong>Замечания проверки</strong>{result.output.detailed_analysis.errors_found.map((issue, i) => <MathText key={i}>{issue}</MathText>)}</div>}
          {result.output.criteria_scores?.map((c, i) => <div key={i} className="text-sm"><strong><MathText inline>{c.criterion_name}</MathText>: {c.score}/{c.max_score}</strong><MathText>{c.comment}</MathText></div>)}
          <p className="text-xs text-muted-foreground break-all">{result.output._metadata.model} · промпт v{result.output._metadata.grader_prompt_version} · версия {result.snapshot_version.slice(0, 12)} · № {result.task_number}</p>
          <Field label="Комментарий преподавателя"><Textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Что проверено верно, какую ошибку нужно исправить?" /></Field>
          <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={saving} onClick={() => review(5)}>Проверка корректна</Button><Button variant="secondary" disabled={saving} onClick={() => review(1)}>Нужна доработка</Button></div>
          {saved && <p className="text-xs text-success">Оценка сохранена в истории.</p>}
<ErrorNote message={error} />
  </Card>;
}
