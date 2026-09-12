import type { Provider } from "./types";

export const essentialToolRoles = [
  { field: "generator_tools_enabled", label: "Генерация заданий" },
  { field: "verifier_tools_enabled", label: "Верификация задач" },
  { field: "tutor_tools_enabled", label: "Разбор со студентом" },
  { field: "decision_tools_enabled", label: "Проверка решений студентов" },
] as const;

export type EssentialToolsSettings = Record<typeof essentialToolRoles[number]["field"], boolean>;

export function essentialToolsDefaults(value: Partial<EssentialToolsSettings>): EssentialToolsSettings {
  return {
    generator_tools_enabled: value.generator_tools_enabled ?? false,
    verifier_tools_enabled: value.verifier_tools_enabled ?? false,
    tutor_tools_enabled: value.tutor_tools_enabled ?? false,
    decision_tools_enabled: value.decision_tools_enabled ?? false,
  };
}

export function essentialToolsConnectionNote(providers: Provider[], modelId: string): string {
  if (!modelId) return "Модель для этой роли не выбрана. Настройку можно сохранить заранее.";
  const provider = providers.find((p) => p.models.some((m) => m.id === modelId));
  const model = provider?.models.find((m) => m.id === modelId);
  if (!provider?.enabled || provider.purpose !== "production" || !model?.enabled) {
    return "Выбранная модель недоступна. Настройка сохранится и будет применяться при доступном подключении с поддержкой вызова инструментов.";
  }
  // The provider catalogue does not expose a tool-calling capability flag.
  return `Провайдер: ${provider.name}. Поддержка вызова инструментов выбранной моделью не подтверждена в каталоге; включение настройки не гарантирует их доступность.`;
}
