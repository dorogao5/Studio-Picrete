import { Eye, FilePlus2, ListChecks, Loader2, Pencil, Plus, RefreshCw, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Select, Spinner, Textarea } from "../../components/ui";
import { apiErrorMessage, assistantsApi, kbApi, sheetsApi } from "../../lib/api";
import type {
  Assistant,
  KnowledgeChunk,
  KnowledgeDocType,
  KnowledgeDocument,
  KnowledgeDocumentDetail,
  Provider,
  ReferenceSheet,
  ReferenceSheetKind,
} from "../../lib/types";

interface Props {
  assistant: Assistant;
  providers: Provider[];
  onProfileChanged: () => Promise<void> | void;
}

const DOC_TYPE_LABELS: Record<KnowledgeDocType, string> = {
  rpd: "РПД",
  notes: "Конспект лекций",
  textbook: "Учебник",
  problem_book: "Задачник",
  reference: "Справочные данные",
  methodical: "Методичка",
  other: "Другое",
};

const SHEET_KIND_LABELS: Record<ReferenceSheetKind, string> = {
  data_table: "Таблица данных",
  glossary: "Глоссарий",
  conventions: "Обозначения",
  formulas: "Формулы",
  other: "Другое",
};

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}

function normTopic(topic: string): string {
  return topic.trim().toLowerCase();
}

export default function MaterialsTab({ assistant, onProfileChanged }: Props) {
  return (
    <div className="space-y-8">
      <DocumentsSection assistant={assistant} onProfileChanged={onProfileChanged} />
      <SheetsSection assistant={assistant} />
    </div>
  );
}

function DocumentsSection({
  assistant,
  onProfileChanged,
}: {
  assistant: Assistant;
  onProfileChanged: () => Promise<void> | void;
}) {
  const [docs, setDocs] = useState<KnowledgeDocument[] | null>(null);
  const [error, setError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState<KnowledgeDocType>("other");
  const [uploading, setUploading] = useState(false);
  const [busyDocId, setBusyDocId] = useState<string | null>(null);
  const [viewDocId, setViewDocId] = useState<string | null>(null);
  const [syllabusDocId, setSyllabusDocId] = useState<string | null>(null);

  const reload = async () => {
    try {
      setDocs(await kbApi.documents(assistant.id));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistant.id]);

  const processing = useMemo(
    () => (docs ?? []).some((d) => d.status === "uploaded" || d.status === "parsing"),
    [docs],
  );

  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(() => void reload(), 3000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processing, assistant.id]);

  const upload = async () => {
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      await kbApi.upload(assistant.id, file, title.trim() || file.name, docType);
      setFile(null);
      setTitle("");
      setFileKey((k) => k + 1);
      await reload();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  const reparse = async (doc: KnowledgeDocument) => {
    setBusyDocId(doc.id);
    setError("");
    try {
      await kbApi.reparse(assistant.id, doc.id);
      await reload();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusyDocId(null);
    }
  };

  const removeDoc = async (doc: KnowledgeDocument) => {
    if (!confirm(`Удалить документ «${doc.title}» вместе с его фрагментами?`)) return;
    setError("");
    try {
      await kbApi.removeDocument(assistant.id, doc.id);
      await reload();
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold">Документы курса</h2>
        <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
          РПД, конспекты, учебники и задачники превращаются в базу знаний: текст распознаётся, режется на фрагменты
          и используется для заземления генерации, проверки и разбора.
        </p>
      </div>

      <Card className="p-4">
        <div className="grid gap-3 items-end sm:grid-cols-2 lg:grid-cols-[1fr_1fr_170px_auto]">
          <Field label="Файл" hint="PDF, Markdown, TXT или фото страниц">
            <Input
              key={fileKey}
              type="file"
              accept=".pdf,.md,.txt,.jpg,.jpeg,.png,.webp"
              onChange={(e) => {
                const next = e.target.files?.[0] ?? null;
                setFile(next);
                if (next) setTitle(next.name);
              }}
            />
          </Field>
          <Field label="Название">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="напр. РПД Неорганическая химия" />
          </Field>
          <Field label="Тип документа">
            <Select value={docType} onChange={(e) => setDocType(e.target.value as KnowledgeDocType)}>
              {Object.entries(DOC_TYPE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Button onClick={upload} loading={uploading} disabled={!file} className="mb-[26px] sm:mb-0">
            <Upload className="h-4 w-4" /> Загрузить
          </Button>
        </div>
      </Card>

      <ErrorNote message={error} />
      {docs === null ? (
        <Spinner />
      ) : docs.length === 0 ? (
        <EmptyState
          title="Документов пока нет"
          hint="Загрузите РПД, конспект или задачник — они станут базой знаний ассистента"
        />
      ) : (
        <div className="space-y-2">
          {docs.map((doc) => (
            <Card key={doc.id} className="p-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-medium truncate">{doc.title}</p>
                    <Badge tone="info">{DOC_TYPE_LABELS[doc.doc_type]}</Badge>
                    <DocStatusBadge doc={doc} />
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {formatSize(doc.size_bytes)} · {new Date(doc.created_at).toLocaleDateString("ru-RU")}
                    {doc.status === "parsed" && ` · ${doc.chunk_count} фрагментов`}
                  </p>
                  {doc.status === "failed" && doc.error && (
                    <p className="text-xs text-destructive mt-1 whitespace-pre-wrap">{doc.error}</p>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0 flex-wrap justify-end">
                  <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setViewDocId(doc.id)}>
                    <Eye className="h-3.5 w-3.5" /> Открыть
                  </Button>
                  {doc.status === "parsed" && (
                    <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setSyllabusDocId(doc.id)}>
                      <ListChecks className="h-3.5 w-3.5" /> Извлечь темы
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    className="px-2 py-1 text-xs"
                    loading={busyDocId === doc.id}
                    disabled={doc.status === "parsing" || doc.status === "uploaded"}
                    onClick={() => reparse(doc)}
                  >
                    <RefreshCw className="h-3.5 w-3.5" /> Переразобрать
                  </Button>
                  <button
                    className="p-1 text-muted-foreground hover:text-destructive"
                    title="Удалить"
                    onClick={() => removeDoc(doc)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {viewDocId && <DocViewModal assistantId={assistant.id} documentId={viewDocId} onClose={() => setViewDocId(null)} />}
      {syllabusDocId && (
        <SyllabusModal
          assistant={assistant}
          documentId={syllabusDocId}
          onClose={() => setSyllabusDocId(null)}
          onProfileChanged={onProfileChanged}
        />
      )}
    </section>
  );
}

function DocStatusBadge({ doc }: { doc: KnowledgeDocument }) {
  if (doc.status === "parsed") return <Badge tone="success">Готов</Badge>;
  if (doc.status === "failed") {
    return (
      <span title={doc.error}>
        <Badge tone="destructive">Ошибка</Badge>
      </span>
    );
  }
  return (
    <Badge tone="warning">
      <Loader2 className="h-3 w-3 mr-1 animate-spin" /> Обрабатывается…
    </Badge>
  );
}

function DocViewModal({
  assistantId,
  documentId,
  onClose,
}: {
  assistantId: string;
  documentId: string;
  onClose: () => void;
}) {
  const [doc, setDoc] = useState<KnowledgeDocumentDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    kbApi
      .document(assistantId, documentId)
      .then(setDoc)
      .catch((err) => setError(apiErrorMessage(err)));
  }, [assistantId, documentId]);

  return (
    <Modal title={doc?.title ?? "Документ"} open onClose={onClose} wide>
      <div className="space-y-3">
        <ErrorNote message={error} />
        {doc === null && !error && <Spinner />}
        {doc &&
          (doc.markdown ? (
            <pre className="whitespace-pre-wrap text-xs font-mono max-h-[65vh] overflow-y-auto rounded-md border border-border bg-muted/30 p-3">
              {doc.markdown}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">Текст ещё не извлечён — дождитесь окончания обработки.</p>
          ))}
      </div>
    </Modal>
  );
}

function SyllabusModal({
  assistant,
  documentId,
  onClose,
  onProfileChanged,
}: {
  assistant: Assistant;
  documentId: string;
  onClose: () => void;
  onProfileChanged: () => Promise<void> | void;
}) {
  const [topics, setTopics] = useState<string[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const existing = useMemo(() => new Set(assistant.topics.map(normTopic)), [assistant.topics]);

  useEffect(() => {
    kbApi
      .extractSyllabus(assistant.id, documentId)
      .then((res) => {
        setTopics(res.topics);
        setSelected(new Set(res.topics.filter((t) => !existing.has(normTopic(t)))));
      })
      .catch((err) => setError(apiErrorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistant.id, documentId]);

  const toggle = (topic: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(topic)) {
        next.delete(topic);
      } else {
        next.add(topic);
      }
      return next;
    });
  };

  const newSelected = (topics ?? []).filter((t) => selected.has(t) && !existing.has(normTopic(t)));

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const merged = [...assistant.topics];
      const seen = new Set(assistant.topics.map(normTopic));
      for (const topic of newSelected) {
        if (!seen.has(normTopic(topic))) {
          merged.push(topic);
          seen.add(normTopic(topic));
        }
      }
      await assistantsApi.update(assistant.id, { topics: merged });
      await onProfileChanged();
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Темы из документа" open onClose={onClose}>
      <div className="space-y-4">
        <ErrorNote message={error} />
        {topics === null && !error && <Spinner label="Извлекаем темы из документа — это может занять минуту..." />}
        {topics && topics.length === 0 && (
          <EmptyState title="Темы не найдены" hint="Модель не смогла выделить разделы курса из этого документа" />
        )}
        {topics && topics.length > 0 && (
          <>
            <p className="text-xs text-muted-foreground">
              Отметьте темы, которые нужно добавить в профиль дисциплины.
            </p>
            <div className="space-y-1.5 max-h-[50vh] overflow-y-auto pr-1">
              {topics.map((topic, i) => {
                const inProfile = existing.has(normTopic(topic));
                return (
                  <label key={i} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={inProfile || selected.has(topic)}
                      disabled={inProfile}
                      onChange={() => toggle(topic)}
                    />
                    <span className={inProfile ? "text-muted-foreground" : ""}>{topic}</span>
                    {inProfile && <Badge className="shrink-0">уже в профиле</Badge>}
                  </label>
                );
              })}
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>
                Отмена
              </Button>
              <Button onClick={save} loading={saving} disabled={newSelected.length === 0}>
                Добавить в профиль ({newSelected.length})
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

function SheetsSection({ assistant }: { assistant: Assistant }) {
  const [sheets, setSheets] = useState<ReferenceSheet[] | null>(null);
  const [error, setError] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorSheet, setEditorSheet] = useState<ReferenceSheet | null>(null);
  const [fromDocOpen, setFromDocOpen] = useState(false);

  const reload = async () => {
    try {
      setSheets(await sheetsApi.list(assistant.id));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistant.id]);

  const removeSheet = async (sheet: ReferenceSheet) => {
    if (!confirm(`Удалить справочник «${sheet.title}»?`)) return;
    setError("");
    try {
      await sheetsApi.remove(assistant.id, sheet.id);
      await reload();
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  return (
    <section className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Справочные материалы курса</h2>
          <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
            ИИ обязан использовать эти данные вместо общесправочных — значения констант, обозначения и терминология
            берутся отсюда.
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button variant="secondary" onClick={() => setFromDocOpen(true)}>
            <FilePlus2 className="h-4 w-4" /> Из документа
          </Button>
          <Button
            onClick={() => {
              setEditorSheet(null);
              setEditorOpen(true);
            }}
          >
            <Plus className="h-4 w-4" /> Справочник
          </Button>
        </div>
      </div>

      <ErrorNote message={error} />
      {sheets === null ? (
        <Spinner />
      ) : sheets.length === 0 ? (
        <EmptyState
          title="Справочных материалов пока нет"
          hint="Добавьте таблицы констант, глоссарий и обозначения курса — вручную или из фрагментов документа"
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {sheets.map((sheet) => (
            <Card key={sheet.id} className="p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-medium truncate">{sheet.title}</p>
                    <Badge tone="info">{SHEET_KIND_LABELS[sheet.kind]}</Badge>
                    {sheet.is_canonical && <Badge tone="accent">канон</Badge>}
                  </div>
                  {sheet.description && <p className="text-xs text-muted-foreground mt-1">{sheet.description}</p>}
                  <p className="text-xs text-muted-foreground mt-1">
                    обновлено {new Date(sheet.updated_at).toLocaleDateString("ru-RU")}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    className="px-2 py-1 text-xs"
                    onClick={() => {
                      setEditorSheet(sheet);
                      setEditorOpen(true);
                    }}
                  >
                    <Pencil className="h-3.5 w-3.5" /> Изменить
                  </Button>
                  <button
                    className="p-1 text-muted-foreground hover:text-destructive"
                    title="Удалить"
                    onClick={() => removeSheet(sheet)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {editorOpen && (
        <SheetEditorModal
          assistantId={assistant.id}
          sheet={editorSheet}
          onClose={() => setEditorOpen(false)}
          onSaved={reload}
        />
      )}
      {fromDocOpen && (
        <FromChunksModal assistantId={assistant.id} onClose={() => setFromDocOpen(false)} onCreated={reload} />
      )}
    </section>
  );
}

function SheetEditorModal({
  assistantId,
  sheet,
  onClose,
  onSaved,
}: {
  assistantId: string;
  sheet: ReferenceSheet | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(sheet?.title ?? "");
  const [kind, setKind] = useState<ReferenceSheetKind>(sheet?.kind ?? "data_table");
  const [description, setDescription] = useState(sheet?.description ?? "");
  const [content, setContent] = useState(sheet?.content_markdown ?? "");
  const [isCanonical, setIsCanonical] = useState(sheet?.is_canonical ?? true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      const body = {
        title,
        kind,
        description,
        content_markdown: content,
        is_canonical: isCanonical,
      };
      if (sheet) {
        await sheetsApi.update(assistantId, sheet.id, body);
      } else {
        await sheetsApi.create(assistantId, body);
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={sheet ? "Справочник" : "Новый справочник"} open onClose={onClose} wide>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Название">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="напр. Энергии Гиббса образования" />
          </Field>
          <Field label="Тип">
            <Select value={kind} onChange={(e) => setKind(e.target.value as ReferenceSheetKind)}>
              {Object.entries(SHEET_KIND_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Описание">
            <Input value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
        <Field label="Содержимое (markdown)" hint="Таблицы и значения попадут в промпты дословно — проверьте единицы и обозначения">
          <Textarea rows={14} value={content} onChange={(e) => setContent(e.target.value)} />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={isCanonical} onChange={(e) => setIsCanonical(e.target.checked)} />
          Канонический источник — включается в промпты генерации, проверки и разбора по умолчанию
        </label>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={saving} disabled={!title.trim() || !content.trim()}>
            Сохранить
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function FromChunksModal({
  assistantId,
  onClose,
  onCreated,
}: {
  assistantId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [docs, setDocs] = useState<KnowledgeDocument[] | null>(null);
  const [docId, setDocId] = useState("");
  const [chunks, setChunks] = useState<KnowledgeChunk[] | null>(null);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<ReferenceSheetKind>("data_table");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    kbApi
      .documents(assistantId)
      .then((all) => {
        const parsed = all.filter((d) => d.status === "parsed");
        setDocs(parsed);
        if (parsed[0]) setDocId(parsed[0].id);
      })
      .catch((err) => setError(apiErrorMessage(err)));
  }, [assistantId]);

  useEffect(() => {
    if (!docId) {
      setChunks(null);
      return;
    }
    setChunksLoading(true);
    setSelected(new Set());
    kbApi
      .chunks(assistantId, docId)
      .then(setChunks)
      .catch((err) => setError(apiErrorMessage(err)))
      .finally(() => setChunksLoading(false));
  }, [assistantId, docId]);

  const toggle = (chunkId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(chunkId)) {
        next.delete(chunkId);
      } else {
        next.add(chunkId);
      }
      return next;
    });
  };

  const tableChunks = (chunks ?? []).filter((c) => c.kind === "table");
  const textChunks = (chunks ?? []).filter((c) => c.kind !== "table");

  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      await sheetsApi.fromChunks(assistantId, {
        document_id: docId,
        chunk_ids: (chunks ?? []).filter((c) => selected.has(c.id)).map((c) => c.id),
        title,
        kind,
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const chunkRow = (chunk: KnowledgeChunk) => (
    <label
      key={chunk.id}
      className="flex items-start gap-2 rounded-md border border-border p-2.5 text-xs cursor-pointer hover:bg-muted/40"
    >
      <input type="checkbox" className="mt-0.5" checked={selected.has(chunk.id)} onChange={() => toggle(chunk.id)} />
      <span className="min-w-0">
        {chunk.heading && <span className="block font-medium mb-0.5">{chunk.heading}</span>}
        <span className="block text-muted-foreground whitespace-pre-wrap break-words">
          {chunk.content.slice(0, 200)}
          {chunk.content.length > 200 ? "…" : ""}
        </span>
      </span>
    </label>
  );

  return (
    <Modal title="Справочник из документа" open onClose={onClose} wide>
      <div className="space-y-4">
        <ErrorNote message={error} />
        {docs === null && !error ? (
          <Spinner />
        ) : docs !== null && docs.length === 0 ? (
          <EmptyState
            title="Нет обработанных документов"
            hint="Сначала загрузите документ и дождитесь окончания обработки"
          />
        ) : (
          docs !== null && (
            <>
              <Field label="Документ">
                <Select value={docId} onChange={(e) => setDocId(e.target.value)}>
                  {docs.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.title}
                    </option>
                  ))}
                </Select>
              </Field>
              {chunksLoading ? (
                <Spinner />
              ) : chunks && chunks.length === 0 ? (
                <EmptyState title="В документе нет фрагментов" />
              ) : (
                chunks && (
                  <div className="space-y-2 max-h-[45vh] overflow-y-auto pr-1">
                    {tableChunks.length > 0 && (
                      <>
                        <p className="text-xs font-semibold text-muted-foreground uppercase">Таблицы</p>
                        {tableChunks.map(chunkRow)}
                      </>
                    )}
                    {textChunks.length > 0 && (
                      <>
                        <p className="text-xs font-semibold text-muted-foreground uppercase border-t border-border pt-2">
                          Текстовые фрагменты
                        </p>
                        {textChunks.map(chunkRow)}
                      </>
                    )}
                  </div>
                )
              )}
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Название справочника">
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} />
                </Field>
                <Field label="Тип">
                  <Select value={kind} onChange={(e) => setKind(e.target.value as ReferenceSheetKind)}>
                    {Object.entries(SHEET_KIND_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">Выбрано фрагментов: {selected.size}</p>
                <div className="flex gap-2">
                  <Button variant="ghost" onClick={onClose}>
                    Отмена
                  </Button>
                  <Button onClick={submit} loading={saving} disabled={!title.trim() || selected.size === 0 || !docId}>
                    Создать справочник
                  </Button>
                </div>
              </div>
            </>
          )
        )}
      </div>
    </Modal>
  );
}
