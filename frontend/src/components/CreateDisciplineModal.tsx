import { useState } from "react";
import { apiErrorMessage, assistantsApi } from "../lib/api";
import type { Assistant } from "../lib/types";
import { Button, ErrorNote, Field, Input, Modal, Textarea } from "./ui";

const DISCIPLINES = [
  "Неорганическая химия",
  "Коллоидная химия",
  "Аналитическая химия",
  "Физическая химия",
  "Физика",
];

export default function CreateDisciplineModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (assistant: Assistant) => void;
}) {
  const [name, setName] = useState("");
  const [discipline, setDiscipline] = useState(DISCIPLINES[0]);
  const [customDiscipline, setCustomDiscipline] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    const disc = customDiscipline.trim() || discipline;
    setLoading(true);
    setError("");
    try {
      const created = await assistantsApi.create({
        name: name.trim() || disc,
        discipline: disc,
        description,
      });
      onCreated(created);
      onClose();
      setName("");
      setDescription("");
      setCustomDiscipline("");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Новая дисциплина" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Предмет">
          <div className="flex flex-wrap gap-1.5">
            {DISCIPLINES.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => {
                  setDiscipline(d);
                  setCustomDiscipline("");
                }}
                className={`rounded-full px-3 py-1 text-xs font-medium border ${
                  discipline === d && !customDiscipline
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-border text-muted-foreground hover:bg-muted"
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </Field>
        <Field label="Или свой предмет">
          <Input
            value={customDiscipline}
            onChange={(e) => setCustomDiscipline(e.target.value)}
            placeholder="напр. Кристаллохимия"
          />
        </Field>
        <Field label="Название рабочей области" hint="Если пусто — возьмётся название предмета">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="напр. Неорганика, 1 курс, поток А" />
        </Field>
        <Field label="Описание и контекст">
          <Textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Уровень группы, учебник, особенности программы..."
          />
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading}>
            Создать
          </Button>
        </div>
      </div>
    </Modal>
  );
}
