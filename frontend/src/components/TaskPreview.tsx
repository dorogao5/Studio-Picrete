import { AlertTriangle, Eye, EyeOff, FileImage, GraduationCap } from "lucide-react";
import { useEffect, useId, useState } from "react";
import type { GeneratedTask, RubricCriterion } from "../lib/types";
import MathText from "./MathText";

type PreviewTask = Pick<
  GeneratedTask,
  "statement" | "reference_solution" | "answer" | "images" | "rubric" | "max_score" | "topic"
>;

export default function TaskPreview({ task, mode }: { task: PreviewTask; mode: "student" | "exam" }) {
  const [showAnswer, setShowAnswer] = useState(false);
  const answerId = useId();

  useEffect(() => setShowAnswer(false), [mode, task.statement, task.answer]);

  return (
    <article className="overflow-hidden rounded-xl border border-border bg-background shadow-soft">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-card px-4 py-3 sm:px-5">
        <div className="flex min-w-0 items-center gap-2">
          {mode === "student" ? <Eye className="h-4 w-4 text-accent" /> : <GraduationCap className="h-4 w-4 text-accent" />}
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              {mode === "student" ? "Карточка студента" : "Экзаменационный лист"}
            </p>
            <MathText className="font-medium">{task.topic || "Задача"}</MathText>
          </div>
        </div>
        <span className="rounded-full border border-border bg-background px-2.5 py-1 text-xs tabular-nums text-muted-foreground">
          {task.max_score} баллов
        </span>
      </header>

      <div className="space-y-4 p-4 sm:p-5">
        <section aria-labelledby={`${answerId}-statement`}>
          <p id={`${answerId}-statement`} className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Условие
          </p>
          <MathText className="text-[15px] leading-7">{task.statement || "Условие не заполнено"}</MathText>
        </section>

        {task.images.length > 0 ? (
          <div className="grid gap-3 sm:grid-cols-2" aria-label="Изображения задачи">
            {task.images.map((source, index) => <PreviewImage key={`${source}-${index}`} source={source} index={index} />)}
          </div>
        ) : null}

        {mode === "exam" ? (
          <div className="rounded-lg border border-dashed border-border bg-card/60 p-4">
            <p className="text-xs font-medium">Место для решения</p>
            <div className="mt-4 space-y-4" aria-hidden="true">
              <div className="border-b border-border" />
              <div className="border-b border-border" />
              <div className="border-b border-border" />
            </div>
          </div>
        ) : (
          <div className="border-t border-border pt-4">
            <button
              type="button"
              aria-expanded={showAnswer}
              aria-controls={answerId}
              className="inline-flex min-h-10 items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
              onClick={() => setShowAnswer((visible) => !visible)}
            >
              {showAnswer ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              {showAnswer ? "Скрыть ответ" : "Показать ответ"}
            </button>
            {showAnswer ? (
              <div id={answerId} className="mt-3 space-y-3 rounded-lg border border-success/25 bg-success/5 p-3.5">
                <div>
                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-success">Краткий ответ</p>
                  <MathText className="font-medium">{task.answer || "Ответ не заполнен"}</MathText>
                </div>
                <details>
                  <summary className="min-h-10 cursor-pointer py-2 text-sm font-medium">Показать эталонное решение</summary>
                  <MathText className="text-sm text-muted-foreground">{task.reference_solution || "Решение не заполнено"}</MathText>
                </details>
              </div>
            ) : null}
          </div>
        )}

        {mode === "exam" && task.rubric.length > 0 ? <RubricSummary rubric={task.rubric} /> : null}
      </div>
    </article>
  );
}

function PreviewImage({ source, index }: { source: string; index: number }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [source]);

  return (
    <figure className="overflow-hidden rounded-lg border border-border bg-card">
      {failed ? (
        <div className="flex min-h-36 flex-col items-center justify-center gap-2 p-4 text-center text-muted-foreground" role="status">
          <AlertTriangle className="h-5 w-5 text-warning" />
          <p className="text-xs font-medium text-foreground">Файл не открылся в Studio</p>
          <p className="break-all text-[11px]">{source}</p>
        </div>
      ) : (
        <img
          src={source}
          alt={`Иллюстрация ${index + 1} к условию задачи`}
          className="max-h-72 w-full bg-white object-contain"
          onError={() => setFailed(true)}
        />
      )}
      <figcaption className="flex items-center gap-1.5 border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
        <FileImage className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{source}</span>
      </figcaption>
    </figure>
  );
}

function RubricSummary({ rubric }: { rubric: RubricCriterion[] }) {
  return (
    <details className="rounded-lg border border-border bg-card px-3.5">
      <summary className="min-h-11 cursor-pointer py-3 text-sm font-medium">Критерии для проверяющего</summary>
      <ul className="space-y-2 border-t border-border py-3 text-xs">
        {rubric.map((criterion, index) => (
          <li key={`${criterion.criterion_name}-${index}`} className="flex items-start justify-between gap-3">
            <MathText inline className="min-w-0">{criterion.criterion_name}</MathText>
            <span className="shrink-0 tabular-nums text-muted-foreground">{criterion.max_score} б.</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
