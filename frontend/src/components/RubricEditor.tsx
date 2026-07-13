import { Plus, Trash2 } from "lucide-react";
import type { RubricCriterion } from "../lib/types";
import { Button, Input, Textarea } from "./ui";

const RUBRIC_TOTAL = 10;
const MAX_CRITERIA = 12;

export function rubricValidationError(rubric: RubricCriterion[]): string {
  if (rubric.length === 0) return "";
  if (rubric.some((criterion) => !criterion.criterion_name.trim())) return "Укажите название каждого критерия";
  if (rubric.some((criterion) => !Number.isFinite(criterion.max_score) || criterion.max_score <= 0)) {
    return "Балл каждого критерия должен быть больше нуля";
  }
  const names = rubric.map((criterion) => criterion.criterion_name.trim().toLocaleLowerCase("ru"));
  if (new Set(names).size !== names.length) return "Названия критериев не должны повторяться";
  const total = rubric.reduce((sum, criterion) => sum + criterion.max_score, 0);
  if (Math.abs(total - RUBRIC_TOTAL) > 0.000001) return `Распределите ровно ${RUBRIC_TOTAL} баллов`;
  return "";
}

export function RubricEditor({
  value,
  onChange,
  disabled = false,
}: {
  value: RubricCriterion[];
  onChange: (value: RubricCriterion[]) => void;
  disabled?: boolean;
}) {
  const total = value.reduce((sum, criterion) => sum + (Number.isFinite(criterion.max_score) ? criterion.max_score : 0), 0);
  const validationError = rubricValidationError(value);

  const updateCriterion = (index: number, patch: Partial<RubricCriterion>) => {
    onChange(value.map((criterion, criterionIndex) => (criterionIndex === index ? { ...criterion, ...patch } : criterion)));
  };

  const addCriterion = () => {
    if (value.length >= MAX_CRITERIA) return;
    const remaining = Math.max(RUBRIC_TOTAL - total, 0);
    onChange([
      ...value,
      {
        criterion_name: "",
        max_score: remaining > 0 ? remaining : 1,
        description: "",
      },
    ]);
  };

  return (
    <section className="space-y-3 rounded-lg border border-border bg-muted/20 p-3 sm:p-4" aria-labelledby="rubric-editor-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="rubric-editor-title" className="text-sm font-medium text-foreground">
            Рубрика задачи
          </h3>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
            Эти критерии и баллы сохраняются в каждой сгенерированной задаче без изменений. Если оставить список пустым,
            рубрику предложит модель.
          </p>
        </div>
        <div className="text-right" aria-live="polite">
          <p className={validationError ? "text-sm font-semibold tabular-nums text-destructive" : "text-sm font-semibold tabular-nums text-foreground"}>
            {total.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} / {RUBRIC_TOTAL} баллов
          </p>
          {value.length > 0 && validationError && <p className="mt-0.5 text-xs text-destructive">{validationError}</p>}
        </div>
      </div>

      {value.length > 0 && (
        <div className="space-y-2.5">
          {value.map((criterion, index) => (
            <div key={index} className="rounded-md border border-border bg-card p-3">
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem_auto] sm:items-end">
                <label className="space-y-1.5">
                  <span className="text-xs font-medium text-muted-foreground">Критерий {index + 1}</span>
                  <Input
                    value={criterion.criterion_name}
                    onChange={(event) => updateCriterion(index, { criterion_name: event.target.value })}
                    placeholder="Например, выбор метода"
                    maxLength={200}
                    disabled={disabled}
                    aria-invalid={!criterion.criterion_name.trim()}
                  />
                </label>
                <label className="space-y-1.5">
                  <span className="text-xs font-medium text-muted-foreground">Баллы</span>
                  <Input
                    type="number"
                    min={0.01}
                    max={RUBRIC_TOTAL}
                    step={0.25}
                    value={criterion.max_score}
                    onChange={(event) => updateCriterion(index, { max_score: Number(event.target.value) })}
                    disabled={disabled}
                    aria-invalid={!Number.isFinite(criterion.max_score) || criterion.max_score <= 0}
                  />
                </label>
                <Button
                  type="button"
                  variant="ghost"
                  className="px-2 text-muted-foreground hover:text-destructive"
                  onClick={() => onChange(value.filter((_, criterionIndex) => criterionIndex !== index))}
                  disabled={disabled}
                  aria-label={`Удалить критерий ${index + 1}`}
                  title="Удалить критерий"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
              <label className="mt-3 block space-y-1.5">
                <span className="text-xs font-medium text-muted-foreground">За что начисляются баллы</span>
                <Textarea
                  rows={2}
                  value={criterion.description}
                  onChange={(event) => updateCriterion(index, { description: event.target.value })}
                  placeholder="Короткое наблюдаемое условие, по которому проверяется работа"
                  maxLength={1000}
                  disabled={disabled}
                />
              </label>
            </div>
          ))}
        </div>
      )}

      <Button
        type="button"
        variant="secondary"
        onClick={addCriterion}
        disabled={disabled || value.length >= MAX_CRITERIA}
      >
        <Plus className="h-4 w-4" />
        Добавить критерий
      </Button>
    </section>
  );
}
