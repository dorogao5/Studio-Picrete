import { useId, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { apiErrorMessage, assistantsApi } from "../../lib/api";
import type { Assistant, Criterion, Provider } from "../../lib/types";
import { Button, Card, ErrorNote, Field, Input, Select, Textarea } from "../../components/ui";

import { modelOptions } from "./PromptsTab";
import { isKnownAdvisoryModel } from "../../lib/modelPolicy";
import { essentialToolRoles, essentialToolsDefaults, essentialToolsConnectionNote } from "../../lib/essentialTools";

function EssentialToolsToggle({ label, checked, note, onChange }: {
  label: string; checked: boolean; note: string; onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return <div className="space-y-1.5">
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-0.5" checked={checked}
        aria-describedby={`${id}-hint`} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
    <p id={`${id}-hint`} className="text-xs text-muted-foreground">{note}</p>
  </div>;
}

export default function ProfileTab({ assistant, providers, onSaved }: { assistant: Assistant; providers: Provider[]; onSaved: () => void }) {
  const models = modelOptions(providers, true);
  const decisionModels = models.filter((model) => !isKnownAdvisoryModel(model));
  const physical = /физичес/i.test(`${assistant.name} ${assistant.discipline}`) && /хим/i.test(`${assistant.name} ${assistant.discipline}`);
  const gradingModels = models.filter((model) => !isKnownAdvisoryModel(model) || (physical && model.family === "qwen"));
  const [verifierModel, setVerifierModel] = useState(assistant.verifier_model_id ?? "");
  const [graderModel, setGraderModel] = useState(assistant.default_grader_model_id ?? "");
  const [generatorModel, setGeneratorModel] = useState(assistant.default_generator_model_id ?? "");
  const [toolsSettings, setToolsSettings] = useState(() => essentialToolsDefaults(assistant));
  const toolRoleModels = {
    generator_tools_enabled: generatorModel,
    verifier_tools_enabled: verifierModel || (physical ? "" : graderModel),
    tutor_tools_enabled: generatorModel,
    decision_tools_enabled: graderModel,
  };
  const [gradingEnabled, setGradingEnabled] = useState(assistant.grading_enabled ?? false);
  const [name, setName] = useState(assistant.name);
  const [description, setDescription] = useState(assistant.description);
  const [audience, setAudience] = useState(assistant.audience);
  const [topics, setTopics] = useState(assistant.topics.join("\n"));
  const [criteria, setCriteria] = useState<Criterion[]>(assistant.criteria);
  const [nuances, setNuances] = useState<string[]>(assistant.nuances);
  const [newNuance, setNewNuance] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await assistantsApi.update(assistant.id, {
        name,
        default_grader_model_id: graderModel || null,
        default_generator_model_id: generatorModel || null,
        verifier_model_id: verifierModel || null,
        ...toolsSettings,
        grading_enabled: gradingEnabled,        description,
        audience,
        topics: topics.split("\n").map((t) => t.trim().replace(/^[•\-–]\s*/, "")).filter(Boolean),
        criteria: criteria.filter((c) => c.name.trim()),
        nuances: nuances.filter(Boolean),
      });
      setSavedAt(Date.now());
      onSaved();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <Card className="p-5 space-y-4">
        <h2 className="font-semibold text-sm">Модели курса</h2>
        <Field label="Проверка решений студентов" hint="Модель оценивает работы студентов в Picrete по промпту «Проверка решений» после публикации курса.">
          <Select aria-label="Проверка решений студентов" value={graderModel} onChange={(e) => setGraderModel(e.target.value)}>
            <option value="">— выберите модель —</option>
            {graderModel && !gradingModels.some((m) => m.id === graderModel) && <option value={graderModel} disabled>Текущая модель недоступна — выберите другую</option>}
            {gradingModels.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </Select>
        </Field>
        <Field label="Генерация заданий и разбор со студентом" hint="Модель создаёт задачи и отвечает студентам в Picrete по промпту «Разбор со студентом».">
          <Select aria-label="Генерация заданий" value={generatorModel} onChange={(e) => setGeneratorModel(e.target.value)}>
            <option value="">— выберите модель —</option>
            {generatorModel && !models.some((m) => m.id === generatorModel) && <option value={generatorModel} disabled>Текущая модель недоступна — выберите другую</option>}
            {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </Select>
        </Field>
        <Field label="Верификация задач" hint="Независимая модель проверяет и исправляет задачи до выдачи студентам. Для физической химии выберите отдельную модель; у других дисциплин по умолчанию используется модель проверки решений.">
          <Select aria-label="Верификация задач" value={verifierModel} onChange={(e) => setVerifierModel(e.target.value)}>
            <option value="">{physical ? "— выберите верификатор —" : "Модель проверки решений"}</option>
            {verifierModel && !decisionModels.some((m) => m.id === verifierModel) && <option value={verifierModel} disabled>Текущая модель недоступна</option>}
            {decisionModels.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </Select>
        </Field>
      </Card>
      <Card className="p-5 space-y-4">
        <h2 className="font-semibold text-sm">Вычисления и справочник</h2>
        <p className="text-xs text-muted-foreground">
          Разрешить модели использовать калькулятор для численных расчётов, SymPy для символьной математики
          и справочник для поиска опорных сведений. Доступность зависит от поддержки вызова инструментов моделью
          и её подключением. Настройки независимы для каждой роли и сохраняются при смене модели.
        </p>
        {essentialToolRoles.map(({ field, label }) => (
          <EssentialToolsToggle key={field} label={label} checked={toolsSettings[field]}
            note={essentialToolsConnectionNote(providers, toolRoleModels[field])}
            onChange={(checked) => setToolsSettings((current) => ({ ...current, [field]: checked }))} />
        ))}
      </Card>
      <Card className="p-5 space-y-4">
        <h2 className="font-semibold text-sm">Профиль дисциплины</h2>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={gradingEnabled} onChange={(e) => setGradingEnabled(e.target.checked)} /><span>Публиковать проверку работ в Picrete по эталонам курса. Перед публикацией потребуется проверенный прогон в Playground.</span></label>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Название">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Аудитория">
            <Input value={audience} onChange={(e) => setAudience(e.target.value)} placeholder="напр. студенты 1 курса химфака" />
          </Field>
        </div>
        <Field label="Описание курса" hint="Учебник, уровень группы, особенности программы — всё это попадёт в системный промпт">
          <Textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="Темы курса" hint="Одна тема на строку. Заполняются автоматически при разборе документов на шаге «Материалы»">
          <Textarea
            rows={Math.min(12, Math.max(3, topics.split("\n").length + 1))}
            value={topics}
            onChange={(e) => setTopics(e.target.value)}
            placeholder={"Строение атома\nХимическая связь\nРастворы\nОкислительно-восстановительные реакции"}
          />
        </Field>
      </Card>

      <Card className="p-5 space-y-3">
        <div>
          <h2 className="font-semibold text-sm">Критерии оценивания</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            По этим критериям ассистент будет выставлять баллы за каждое решение
          </p>
        </div>
        {criteria.map((criterion, index) => (
          <div key={index} className="grid grid-cols-[minmax(0,1fr)_5rem_auto] items-start gap-2 sm:grid-cols-[14rem_5rem_minmax(0,1fr)_auto]">
            <Input
              value={criterion.name}
              onChange={(e) => setCriteria(criteria.map((c, i) => (i === index ? { ...c, name: e.target.value } : c)))}
              placeholder="Название критерия"
            />
            <Input
              type="number"
              min={0}
              step={0.5}
              value={criterion.max_score}
              onChange={(e) =>
                setCriteria(criteria.map((c, i) => (i === index ? { ...c, max_score: Number(e.target.value) } : c)))
              }
              title="Максимальный балл"
            />
            <Input
              value={criterion.description}
              onChange={(e) =>
                setCriteria(criteria.map((c, i) => (i === index ? { ...c, description: e.target.value } : c)))
              }
              placeholder="За что начисляются баллы"
              className="col-span-3 sm:col-span-1"
            />
            <button
              className="col-start-3 row-start-1 p-2 text-muted-foreground hover:text-destructive sm:col-start-auto sm:row-start-auto"
              aria-label={`Удалить критерий «${criterion.name || index + 1}»`}
              onClick={() => setCriteria(criteria.filter((_, i) => i !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
        <Button variant="secondary" onClick={() => setCriteria([...criteria, { name: "", max_score: 1, description: "" }])}>
          <Plus className="h-4 w-4" /> Критерий
        </Button>
      </Card>

      <Card className="p-5 space-y-3">
        <div>
          <h2 className="font-semibold text-sm">Нюансы проверки</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Экспертные тонкости, которые обычная модель не знает: «за отсутствие условий стандартности снимать 0.5 балла»,
            «допускается запись через эквиваленты», «значащие цифры обязательны»
          </p>
        </div>
        {nuances.map((nuance, index) => (
          <div key={index} className="flex gap-2">
            <Input
              value={nuance}
              onChange={(e) => setNuances(nuances.map((n, i) => (i === index ? e.target.value : n)))}
            />
            <button
              className="p-2 text-muted-foreground hover:text-destructive"
              onClick={() => setNuances(nuances.filter((_, i) => i !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
        <div className="flex gap-2">
          <Input
            value={newNuance}
            onChange={(e) => setNewNuance(e.target.value)}
            placeholder="Новый нюанс..."
            onKeyDown={(e) => {
              if (e.key === "Enter" && newNuance.trim()) {
                setNuances([...nuances, newNuance.trim()]);
                setNewNuance("");
              }
            }}
          />
          <Button
            variant="secondary"
            onClick={() => {
              if (newNuance.trim()) {
                setNuances([...nuances, newNuance.trim()]);
                setNewNuance("");
              }
            }}
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </Card>

      <ErrorNote message={error} />
      <div className="flex items-center gap-3">
        <Button onClick={save} loading={saving}>
          Сохранить профиль
        </Button>
        {savedAt && <span className="text-xs text-success">Сохранено</span>}
      </div>
    </div>
  );
}
