import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";
import * as tools from "../src/lib/essentialTools.ts";
import type { Provider } from "../src/lib/types.ts";

const providers = ["yandex", "openai", "custom"].map((kind) => ({
  id: kind, name: kind, kind, purpose: "production", enabled: true, base_url: "", has_api_key: true,
  models: [{ id: kind + "-model", provider_id: kind, model_id: "arbitrary-name", display_name: kind,
    family: "custom", supports_vision: false, supports_json: true, enabled: true, notes: "" }],
})) as Provider[];
const fields = tools.essentialToolRoles.map((role) => role.field);

test("all four defaults are false and provider metadata never invents tool-calling support", () => {
  assert.deepEqual(Object.values(tools.essentialToolsDefaults({})), [false, false, false, false]);
  for (const provider of providers) {
    assert.match(tools.essentialToolsConnectionNote(providers, provider.models[0].id), /не подтверждена/);
  }
  assert.match(tools.essentialToolsConnectionNote(providers, ""), /не выбрана/);
  assert.match(tools.essentialToolsConnectionNote(providers, "missing"), /недоступна/);
});

// Run actual profile controls and save handlers against an inert API.
const require = createRequire(import.meta.url);
const primitive = () => null;
function profile(overrides: Record<string, unknown> = {}, saveFailure?: Error) {
  const states: unknown[] = [];
  let index = 0;
  const updates: Array<Record<string, unknown>> = [];
  const imports: Record<string, unknown> = {
    react: { useId: () => "hint", useState: (initial: unknown) => {
      const slot = index++;
      if (!(slot in states)) states[slot] = typeof initial === "function" ? initial() : initial;
      return [states[slot], (value: unknown) => { states[slot] = typeof value === "function" ? value(states[slot]) : value; }];
    } },
    "lucide-react": { Plus: primitive, Trash2: primitive },
    "../../components/ui": Object.fromEntries(["Button", "Card", "ErrorNote", "Field", "Input", "Select", "Textarea"].map((key) => [key, primitive])),
    "../../lib/api": { apiErrorMessage: String, assistantsApi: { update: async (_id: string, payload: Record<string, unknown>) => { updates.push(payload); if (saveFailure) throw saveFailure; } } },
    "../../lib/modelPolicy": { isKnownAdvisoryModel: () => false },
    "../../lib/essentialTools": tools,
    "./PromptsTab": { modelOptions: () => providers.flatMap((p) => p.models.map((m) => ({ id: m.id, label: m.display_name }))) },
  };
  const source = readFileSync(new URL("../src/pages/assistant/ProfileTab.tsx", import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const exports: { default?: (props: unknown) => unknown } = {};
  new Function("require", "exports", outputText)((name: string) => imports[name] ?? require(name), exports);
  const assistant = { id: "assistant", name: "Математика", discipline: "Математика", description: "", audience: "", topics: [], criteria: [], nuances: [], default_generator_model_id: "custom-model", verifier_model_id: "openai-model", default_grader_model_id: "yandex-model", ...overrides };
  function render() {
    index = 0;
    const nodes: any[] = [];
    function walk(node: any) {
      if (Array.isArray(node)) return node.forEach(walk);
      if (!node || typeof node !== "object") return;
      if (node.type?.name === "EssentialToolsToggle") return walk(node.type(node.props));
      nodes.push(node);
      walk(node.props?.children);
    }
    walk(exports.default!({ assistant, providers, onSaved: () => {} }));
    return {
      toggles: nodes.filter((n) => n.type === "input" && n.props.type === "checkbox" && n.props["aria-describedby"]),
      messages: nodes.filter((n) => typeof n.props?.message === "string").map((n) => n.props.message),
      select: (name: string) => nodes.find((n) => n.props["aria-label"] === name),
      save: () => nodes.find((n) => n.props.children === "Сохранить профиль").props.onClick(),
    };
  }
  return { render, updates };
}

test("each role toggles independently without changing models or other role flags", async () => {
  for (const [index, field] of fields.entries()) {
    const view = profile();
    assert.deepEqual(view.render().toggles.map((t) => t.props.checked), [false, false, false, false]);
    view.render().toggles[index].props.onChange({ target: { checked: true } });
    await view.render().save();
    for (const key of fields) assert.equal(view.updates[0][key], key === field);
    assert.equal(view.updates[0].default_generator_model_id, "custom-model");
    assert.equal(view.updates[0].verifier_model_id, "openai-model");
    assert.equal(view.updates[0].default_grader_model_id, "yandex-model");
    assert.ok(!Object.keys(view.updates[0]).some((key) => key.includes("code_interpreter") || key.includes("essential_tools")));
  }
});

test("provider rejection remains visible and never resets role choices or models", async () => {
  const failure = new Error("Провайдер отклонил вызов инструментов: tool calling unsupported");
  const view = profile({ tutor_tools_enabled: true }, failure);
  view.render().toggles[0].props.onChange({ target: { checked: true } });
  await view.render().save();
  assert.ok(view.render().messages.includes(String(failure)));
  assert.deepEqual(view.render().toggles.map((t) => t.props.checked), [true, false, true, false]);
  assert.equal(view.render().select("Генерация заданий").props.value, "custom-model");
  assert.equal(view.render().select("Верификация задач").props.value, "openai-model");
  assert.equal(view.render().select("Проверка решений студентов").props.value, "yandex-model");
});

test("persisted role choices survive model changes and remain configurable for every course", async () => {
  for (const discipline of ["Математика", "Физическая химия"]) {
    const view = profile({ discipline, name: discipline, generator_tools_enabled: true, tutor_tools_enabled: true, decision_tools_enabled: true });
    view.render().select("Генерация заданий").props.onChange({ target: { value: "openai-model" } });
    view.render().select("Верификация задач").props.onChange({ target: { value: "" } });
    assert.deepEqual(view.render().toggles.map((t) => t.props.checked), [true, false, true, true]);
    assert.ok(view.render().toggles.every((t) => !t.props.disabled));
    view.render().toggles[2].props.onChange({ target: { checked: false } });
    await view.render().save();
    assert.deepEqual(fields.map((key) => view.updates[0][key]), [true, false, false, true]);
    assert.equal(view.updates[0].default_generator_model_id, "openai-model");
    assert.equal(view.updates[0].verifier_model_id, null);
  }
});
