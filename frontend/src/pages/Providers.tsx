import { useEffect, useState } from "react";
import { CheckCircle2, Plus, RefreshCw, Trash2, Wallet, XCircle } from "lucide-react";
import { apiErrorMessage, providersApi } from "../lib/api";
import type { Provider, ProviderBalance, ProviderPreset } from "../lib/types";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Select, Spinner } from "../components/ui";

const FAMILIES = ["deepseek", "qwen", "yandexgpt", "alice", "gpt", "generic"];

export default function Providers() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [presets, setPresets] = useState<ProviderPreset[]>([]);
  const [error, setError] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [testing, setTesting] = useState<string | null>(null);
  const [balances, setBalances] = useState<Record<string, ProviderBalance | null>>({});

  const loadBalances = (list: Provider[]) => {
    for (const p of list) {
      setBalances((prev) => ({ ...prev, [p.id]: prev[p.id] ?? null }));
      providersApi
        .balance(p.id)
        .then((b) => setBalances((prev) => ({ ...prev, [p.id]: b })))
        .catch(() =>
          setBalances((prev) => ({
            ...prev,
            [p.id]: { supported: false, ok: false, balance: "", message: "не удалось запросить" },
          })),
        );
    }
  };

  const reload = async () => {
    try {
      const list = await providersApi.list();
      setProviders(list);
      loadBalances(list);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    void reload();
    providersApi.presets().then(setPresets).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runTest = async (id: string) => {
    setTesting(id);
    try {
      const result = await providersApi.test(id);
      setTestResults((prev) => ({ ...prev, [id]: result }));
    } catch (err) {
      setTestResults((prev) => ({ ...prev, [id]: { ok: false, message: apiErrorMessage(err) } }));
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="max-w-4xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Провайдеры LLM</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Подключения к Yandex AI Studio, DeepSeek, Alibaba Model Studio и другим OpenAI-совместимым API
          </p>
        </div>
        <Button onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" /> Добавить
        </Button>
      </div>

      <ErrorNote message={error} />
      {providers === null ? (
        <Spinner />
      ) : providers.length === 0 ? (
        <EmptyState title="Провайдеры не подключены" hint="Добавьте провайдера из пресета и вставьте API-ключ" />
      ) : (
        <div className="space-y-4">
          {providers.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              onChanged={reload}
              onTest={() => runTest(provider.id)}
              testing={testing === provider.id}
              testResult={testResults[provider.id]}
              balance={balances[provider.id]}
            />
          ))}
        </div>
      )}

      <AddProviderModal open={addOpen} onClose={() => setAddOpen(false)} presets={presets} onCreated={reload} />
    </div>
  );
}

function BalanceChip({ balance }: { balance: ProviderBalance | null | undefined }) {
  if (balance === null || balance === undefined) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
        <Wallet className="h-3 w-3" /> …
      </span>
    );
  }
  if (balance.ok) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full border border-success/30 bg-success/10 px-2.5 py-0.5 text-xs font-medium text-success"
        title="Баланс аккаунта у провайдера"
      >
        <Wallet className="h-3 w-3" /> {balance.balance}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground cursor-help"
      title={balance.message || "Баланс недоступен"}
    >
      <Wallet className="h-3 w-3" /> —
    </span>
  );
}

function ProviderCard({
  provider,
  onChanged,
  onTest,
  testing,
  testResult,
  balance,
}: {
  provider: Provider;
  onChanged: () => void;
  onTest: () => void;
  testing: boolean;
  testResult?: { ok: boolean; message: string };
  balance?: ProviderBalance | null;
}) {
  const [newModelId, setNewModelId] = useState("");
  const [newFamily, setNewFamily] = useState("generic");
  const [keyInput, setKeyInput] = useState("");
  const [error, setError] = useState("");

  const addModel = async () => {
    if (!newModelId.trim()) return;
    try {
      await providersApi.addModel(provider.id, { model_id: newModelId.trim(), family: newFamily });
      setNewModelId("");
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  const saveKey = async () => {
    if (!keyInput.trim()) return;
    try {
      await providersApi.update(provider.id, { api_key: keyInput.trim() });
      setKeyInput("");
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-semibold">{provider.name}</h2>
            {provider.purpose === "architect" ? (
              <Badge tone="accent">архитектор промптов</Badge>
            ) : (
              <Badge tone="info">production</Badge>
            )}
            {provider.has_api_key ? <Badge tone="success">ключ задан</Badge> : <Badge tone="warning">нет ключа</Badge>}
            <BalanceChip balance={balance} />
          </div>
          <p className="text-xs text-muted-foreground font-mono mt-1">{provider.base_url}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={onTest} loading={testing}>
            <RefreshCw className="h-3.5 w-3.5" /> Проверить
          </Button>
          <Button
            variant="destructive"
            onClick={async () => {
              if (confirm(`Удалить провайдера «${provider.name}»?`)) {
                await providersApi.remove(provider.id);
                onChanged();
              }
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {testResult && (
        <div
          className={`flex items-start gap-2 rounded-md px-3 py-2 text-sm ${
            testResult.ok ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive"
          }`}
        >
          {testResult.ok ? <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" /> : <XCircle className="h-4 w-4 mt-0.5 shrink-0" />}
          {testResult.message}
        </div>
      )}

      <div className="flex gap-2 items-end">
        <Field label={provider.has_api_key ? "Заменить API-ключ" : "API-ключ"}>
          <Input
            type="password"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder="вставьте ключ"
            className="w-72"
          />
        </Field>
        <Button variant="secondary" onClick={saveKey} disabled={!keyInput.trim()}>
          Сохранить
        </Button>
      </div>

      <div>
        <p className="text-sm font-medium mb-2">Модели</p>
        {provider.models.length === 0 && <p className="text-xs text-muted-foreground mb-2">Модели не добавлены</p>}
        <div className="space-y-1.5">
          {provider.models.map((model) => (
            <div key={model.id} className="flex items-center justify-between rounded-md border border-border px-3 py-1.5">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-sm font-mono truncate">{model.model_id}</span>
                <Badge>{model.family}</Badge>
                {model.supports_vision && <Badge tone="info">vision</Badge>}
                {model.supports_json && <Badge tone="success">json</Badge>}
              </div>
              <button
                className="text-muted-foreground hover:text-destructive p-1"
                onClick={async () => {
                  await providersApi.removeModel(provider.id, model.id);
                  onChanged();
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
        <div className="flex gap-2 mt-2">
          <Input
            value={newModelId}
            onChange={(e) => setNewModelId(e.target.value)}
            placeholder="model id, напр. deepseek-v4-flash"
            className="font-mono"
          />
          <Select value={newFamily} onChange={(e) => setNewFamily(e.target.value)} className="w-40">
            {FAMILIES.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </Select>
          <Button variant="secondary" onClick={addModel} disabled={!newModelId.trim()}>
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <ErrorNote message={error} />
    </Card>
  );
}

function AddProviderModal({
  open,
  onClose,
  presets,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  presets: ProviderPreset[];
  onCreated: () => void;
}) {
  const [presetKind, setPresetKind] = useState("yandex");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [folderId, setFolderId] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const preset = presets.find((p) => p.kind === presetKind);
  const needsFolderId = presetKind === "yandex";

  useEffect(() => {
    if (preset) {
      setName(preset.title);
      setBaseUrl(preset.base_url);
    }
  }, [presetKind, preset]);

  const submit = async () => {
    if (!preset) return;
    setLoading(true);
    setError("");
    try {
      const created = await providersApi.create({
        name: name || preset.title,
        kind: preset.kind,
        purpose: preset.purpose,
        base_url: baseUrl,
        api_key: apiKey,
      });
      for (const model of preset.models) {
        const modelId = needsFolderId ? model.model_id.replace("FOLDER_ID", folderId || "FOLDER_ID") : model.model_id;
        await providersApi.addModel(created.id, {
          model_id: modelId,
          display_name: model.display_name,
          family: model.family,
          supports_vision: model.supports_vision,
          supports_json: model.supports_json,
        });
      }
      onCreated();
      onClose();
      setApiKey("");
      setFolderId("");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Подключить провайдера" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Провайдер">
          <Select value={presetKind} onChange={(e) => setPresetKind(e.target.value)}>
            {presets.map((p) => (
              <option key={p.kind} value={p.kind}>
                {p.title}
              </option>
            ))}
          </Select>
        </Field>
        {preset?.auth_note && <p className="text-xs text-muted-foreground">{preset.auth_note}</p>}
        <Field label="Название">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Base URL">
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="font-mono" />
        </Field>
        {needsFolderId && (
          <Field label="Yandex Cloud folder_id" hint="Подставится в идентификаторы моделей gpt://<folder_id>/...">
            <Input value={folderId} onChange={(e) => setFolderId(e.target.value)} className="font-mono" />
          </Field>
        )}
        <Field label="API-ключ">
          <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading}>
            Подключить
          </Button>
        </div>
      </div>
    </Modal>
  );
}
