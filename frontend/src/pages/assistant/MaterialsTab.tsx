import { Eye, FileText, Loader2, Pencil, Plus, RefreshCw, ScanLine, Sparkles, Trash2, UploadCloud, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Select, Spinner, Textarea } from "../../components/ui";
import MathText from "../../components/MathText";
import { apiErrorMessage, assistantsApi, kbApi, sheetsApi } from "../../lib/api";
import type {
  Assistant,
  DocumentAnalysis,
  KnowledgeChunk,
  KnowledgeDocType,
  KnowledgeDocument,
  KnowledgeDocumentDetail,
  MaterialAuthority,
  MaterialVisibility,
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

const AUTHORITY_LABELS: Record<MaterialAuthority, string> = {
  course_policy: "Правила курса",
  course_lecture: "Материал курса",
  reference: "Справочный источник",
  unverified: "Не проверен",
};

const VISIBILITY_LABELS: Record<MaterialVisibility, string> = {
  student: "Ассистенту студента",
  teacher_only: "Только преподавателю",
  assessment_private: "Закрытый банк",
  quarantine: "Карантин",
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

const ACCEPTED = ".pdf,.md,.txt,.jpg,.jpeg,.png,.webp";

function detectDocType(filename: string): KnowledgeDocType {
  const f = filename.toLowerCase();
  if (/рпд|rpd|рабочая[\s_]*программа/.test(f)) return "rpd";
  if (/лекци|конспект|lektsi|lecture/.test(f)) return "notes";
  if (/задачник|сборник|билет|практикум|задани|тест|exam/.test(f)) return "problem_book";
  if (/учебник|textbook|пособие/.test(f)) return "textbook";
  if (/справоч|таблиц|констант/.test(f)) return "reference";
  if (/метод/.test(f)) return "methodical";
  return "other";
}

function defaultAuthority(docType: KnowledgeDocType): MaterialAuthority {
  if (docType === "rpd" || docType === "methodical") return "course_policy";
  if (docType === "notes") return "course_lecture";
  return "reference";
}

function cleanTitle(filename: string): string {
  return filename
    .replace(/\.[^.]+$/, "")
    .replace(/^\[\d+\]\s*/, "")
    .replace(/^\d{4,}[_\s-]*/, "")
    .replace(/_+/g, " ")
    .trim();
}

export default function MaterialsTab({ assistant, onProfileChanged }: Props) {
  const [sheetsRefresh, setSheetsRefresh] = useState(0);
  return (
    <div className="space-y-8">
      <DocumentsSection
        assistant={assistant}
        onProfileChanged={onProfileChanged}
        onSheetsChanged={() => setSheetsRefresh((k) => k + 1)}
      />
      <SheetsSection assistant={assistant} refreshKey={sheetsRefresh} />
    </div>
  );
}

function DocumentsSection({
  assistant,
  onProfileChanged,
  onSheetsChanged,
}: {
  assistant: Assistant;
  onProfileChanged: () => Promise<void> | void;
  onSheetsChanged: () => void;
}) {
  const [docs, setDocs] = useState<KnowledgeDocument[] | null>(null);
  const [error, setError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState<KnowledgeDocType>("other");
  const [autoAnalyze, setAutoAnalyze] = useState(true);
  const [authority, setAuthority] = useState<MaterialAuthority>("reference");
  const [visibility, setVisibility] = useState<MaterialVisibility>("student");
  const [effectiveVersion, setEffectiveVersion] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [busyDocId, setBusyDocId] = useState<string | null>(null);
  const [viewDocId, setViewDocId] = useState<string | null>(null);
  const [analyzeDoc, setAnalyzeDoc] = useState<KnowledgeDocument | null>(null);
  const pickerRef = useRef<HTMLInputElement>(null);

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
    () =>
      (docs ?? []).some(
        (d) => d.status === "uploaded" || d.status === "parsing" || d.analysis_status === "running",
      ),
    [docs],
  );

  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(() => void reload(), 3000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processing, assistant.id]);

  const pickFile = (next: File | null) => {
    setFile(next);
    if (next) {
      setTitle(cleanTitle(next.name));
      const detectedType = detectDocType(next.name);
      setDocType(detectedType);
      setAuthority(defaultAuthority(detectedType));
    }
  };

  const upload = async () => {
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      await kbApi.upload(assistant.id, file, title.trim() || file.name, docType, {
        analyze: autoAnalyze,
        authority,
        visibility,
        courseScope: assistant.discipline,
        effectiveVersion,
      });
      setFile(null);
      setTitle("");
      setEffectiveVersion("");
      if (pickerRef.current) pickerRef.current.value = "";
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
        <h2 className="text-sm font-semibold">Материалы курса</h2>
        <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
          Загрузите РПД, конспекты, учебники, задачники — Studio извлечёт текст, сама разберёт документ и предложит
          темы, справочные данные и нотацию курса. Останется просмотреть и применить.
        </p>
      </div>

      <input
        ref={pickerRef}
        type="file"
        accept={ACCEPTED}
        className="hidden"
        onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
      />
      {!file ? (
        <button
          className={`w-full rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
            dragOver ? "border-accent bg-accent/5" : "border-border bg-card hover:border-accent/50 hover:bg-muted/30"
          }`}
          onClick={() => pickerRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            pickFile(e.dataTransfer.files?.[0] ?? null);
          }}
        >
          <UploadCloud className="mx-auto h-7 w-7 text-muted-foreground" />
          <p className="mt-2 text-sm font-medium">Перетащите файл сюда или нажмите, чтобы выбрать</p>
          <p className="mt-1 text-xs text-muted-foreground">
            PDF, Markdown, TXT или фото страниц — название и тип определятся автоматически
          </p>
        </button>
      ) : (
        <Card className="p-3.5 space-y-3">
          <div className="flex items-center gap-3 flex-wrap">
            <FileText className="h-5 w-5 shrink-0 text-muted-foreground" />
            <span className="text-xs text-muted-foreground shrink-0">
              {file.name} · {formatSize(file.size)}
            </span>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="flex-1 min-w-[14rem]"
              placeholder="Название документа"
            />
            <Select
              value={docType}
              onChange={(e) => setDocType(e.target.value as KnowledgeDocType)}
              className="w-44 shrink-0"
            >
              {Object.entries(DOC_TYPE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
            <Button onClick={upload} loading={uploading} disabled={!title.trim()}>
              <UploadCloud className="h-4 w-4" /> Загрузить
            </Button>
            <button
              className="p-1.5 text-muted-foreground hover:text-foreground"
              title="Отменить"
              onClick={() => {
                setFile(null);
                if (pickerRef.current) pickerRef.current.value = "";
              }}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <label className="flex items-start gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={autoAnalyze && visibility !== "quarantine"}
              disabled={visibility === "quarantine"}
              onChange={(event) => setAutoAnalyze(event.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-accent"
            />
            <span>
              <span className="font-medium text-foreground">Предложить темы и справочники после загрузки</span>
              <span className="mt-0.5 block">
                {visibility === "quarantine"
                  ? "Для карантина автоматический разбор отключён. Его можно запустить вручную после проверки источника."
                  : "Studio использует выбранную модель ассистента. Разбор можно отключить сейчас и запустить позже вручную."}
              </span>
            </span>
          </label>
          <div className="grid gap-3 border-t border-border pt-3 sm:grid-cols-3">
            <Field label="Статус источника">
              <Select
                value={authority}
                onChange={(event) => {
                  const next = event.target.value as MaterialAuthority;
                  setAuthority(next);
                  if (next === "unverified") setVisibility("quarantine");
                }}
              >
                {Object.entries(AUTHORITY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </Select>
            </Field>
            <Field label="Кому доступен">
              <Select value={visibility} onChange={(event) => setVisibility(event.target.value as MaterialVisibility)}>
                {Object.entries(VISIBILITY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </Select>
            </Field>
            <Field label="Версия / семестр">
              <Input
                value={effectiveVersion}
                onChange={(event) => setEffectiveVersion(event.target.value)}
                placeholder="напр. осень 2026"
              />
            </Field>
          </div>
        </Card>
      )}

      <ErrorNote message={error} />
      {docs === null ? (
        <Spinner />
      ) : docs.length === 0 ? (
        <div className="rounded-lg border border-border bg-muted/20 p-4">
          <p className="text-sm font-medium">Как это работает</p>
          <ol className="mt-2 space-y-1.5 text-xs text-muted-foreground list-decimal pl-4">
            <li>Загрузите документы курса — текст извлечётся автоматически (OCR только для сканов).</li>
            <li>Studio сама разберёт каждый документ: темы, справочные таблицы, обозначения, формулы.</li>
            <li>Нажмите «Разбор готов» и примените — профиль и справочники заполнятся в один клик.</li>
          </ol>
        </div>
      ) : (
        <div className="space-y-2">
          {docs.map((doc) => (
            <Card key={doc.id} className="p-3.5">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-medium truncate">{doc.title}</p>
                    <Badge tone="info">{DOC_TYPE_LABELS[doc.doc_type]}</Badge>
                    <Badge>{AUTHORITY_LABELS[doc.authority]}</Badge>
                    {doc.visibility !== "student" && (
                      <Badge tone={doc.visibility === "quarantine" ? "destructive" : "warning"}>
                        {VISIBILITY_LABELS[doc.visibility]}
                      </Badge>
                    )}
                    <DocStatusBadge doc={doc} />
                    {doc.status === "parsed" && doc.extract_method === "ocr" && (
                      <span title="Текстового слоя не было — распознавали через OCR">
                        <Badge tone="warning">
                          <ScanLine className="h-3 w-3 mr-1" /> OCR
                        </Badge>
                      </span>
                    )}
                    {doc.status === "parsed" && doc.extract_method === "text" && (
                      <span title="Извлечён текстовый слой — без дорогого распознавания">
                        <Badge>
                          <FileText className="h-3 w-3 mr-1" /> текст
                        </Badge>
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {formatSize(doc.size_bytes)} · {new Date(doc.created_at).toLocaleDateString("ru-RU")}
                    {doc.page_count > 0 && ` · ${doc.page_count} стр.`}
                    {doc.status === "parsed" && ` · ${doc.chunk_count} фрагментов`}
                  </p>
                  {doc.status === "failed" && doc.error && (
                    <p className="text-xs text-destructive mt-1 whitespace-pre-wrap">{doc.error}</p>
                  )}
                </div>
                <div className="flex w-full shrink-0 flex-wrap items-center justify-end gap-1 border-t border-border pt-2 sm:w-auto sm:border-0 sm:pt-0">
                  {doc.status === "parsed" && doc.analysis_status === "ready" && (
                    <Button
                      variant="accent"
                      className="min-h-11 px-2.5 py-1 text-xs sm:min-h-0"
                      onClick={() => setAnalyzeDoc(doc)}
                    >
                      <Sparkles className="h-3.5 w-3.5" /> Разбор готов — применить
                    </Button>
                  )}
                  {doc.status === "parsed" && doc.analysis_status === "applied" && (
                    <Button
                      variant="secondary"
                      className="min-h-11 px-2.5 py-1 text-xs sm:min-h-0"
                      onClick={() => setAnalyzeDoc(doc)}
                    >
                      <Sparkles className="h-3.5 w-3.5" /> Разбор применён
                    </Button>
                  )}
                  {doc.status === "parsed" && doc.analysis_status === "running" && (
                    <span className="flex items-center gap-1.5 px-2 text-xs text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Анализируем…
                    </span>
                  )}
                  {doc.status === "parsed" &&
                    (doc.analysis_status === "none" || doc.analysis_status === "failed") && (
                      <span title={doc.analysis_error || undefined}>
                        <Button
                          variant="ghost"
                          className="min-h-11 px-2.5 py-1 text-xs sm:min-h-0"
                          onClick={() => setAnalyzeDoc(doc)}
                        >
                          <Sparkles className="h-3.5 w-3.5" /> Разобрать
                        </Button>
                      </span>
                    )}
                  <button
                    className="flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground sm:h-auto sm:w-auto sm:p-1.5"
                    title="Открыть текст"
                    onClick={() => setViewDocId(doc.id)}
                  >
                    <Eye className="h-4 w-4" />
                  </button>
                  <button
                    className="flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40 sm:h-auto sm:w-auto sm:p-1.5"
                    title="Переразобрать файл"
                    disabled={doc.status === "parsing" || doc.status === "uploaded" || busyDocId === doc.id}
                    onClick={() => reparse(doc)}
                  >
                    {busyDocId === doc.id ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <RefreshCw className="h-4 w-4" />
                    )}
                  </button>
                  <button
                    className="flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-destructive sm:h-auto sm:w-auto sm:p-1.5"
                    title="Удалить"
                    onClick={() => removeDoc(doc)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {viewDocId && <DocViewModal assistantId={assistant.id} documentId={viewDocId} onClose={() => setViewDocId(null)} />}
      {analyzeDoc && (
        <AnalyzeModal
          assistant={assistant}
          document={analyzeDoc}
          onClose={() => setAnalyzeDoc(null)}
          onProfileChanged={onProfileChanged}
          onSheetsChanged={onSheetsChanged}
          onApplied={reload}
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

function AnalyzeModal({
  assistant,
  document,
  onClose,
  onProfileChanged,
  onSheetsChanged,
  onApplied,
}: {
  assistant: Assistant;
  document: KnowledgeDocument;
  onClose: () => void;
  onProfileChanged: () => Promise<void> | void;
  onSheetsChanged: () => void;
  onApplied: () => Promise<void> | void;
}) {
  const [data, setData] = useState<DocumentAnalysis | null>(null);
  const [error, setError] = useState("");
  const [topicSel, setTopicSel] = useState<Set<string>>(new Set());
  const [sheetSel, setSheetSel] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [addNotation, setAddNotation] = useState(true);
  const [useDescription, setUseDescription] = useState(false);
  const [applying, setApplying] = useState(false);

  const existing = useMemo(() => new Set(assistant.topics.map(normTopic)), [assistant.topics]);

  const load = (refresh: boolean) => {
    setData(null);
    setError("");
    kbApi
      .analyze(assistant.id, document.id, refresh)
      .then((res) => {
        setData(res);
        setTopicSel(
          new Set(document.visibility === "quarantine" ? [] : res.topics.filter((t) => !existing.has(normTopic(t)))),
        );
        setSheetSel(new Set(document.visibility === "quarantine" ? [] : res.sheets.map((_, i) => i)));
        setUseDescription(
          document.visibility !== "quarantine" && !assistant.description.trim() && Boolean(res.summary.trim()),
        );
      })
      .catch((err) => setError(apiErrorMessage(err)));
  };

  useEffect(() => {
    load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistant.id, document.id]);

  const toggleTopic = (topic: string) =>
    setTopicSel((prev) => {
      const next = new Set(prev);
      next.has(topic) ? next.delete(topic) : next.add(topic);
      return next;
    });
  const toggleSheet = (i: number) =>
    setSheetSel((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  const toggleExpand = (i: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });

  const newTopics = (data?.topics ?? []).filter((t) => topicSel.has(t) && !existing.has(normTopic(t)));
  const selectedSheets = (data?.sheets ?? []).filter((_, i) => sheetSel.has(i));
  const totalToApply =
    newTopics.length +
    selectedSheets.length +
    (addNotation && data?.notation_notes ? 1 : 0) +
    (useDescription && data?.summary ? 1 : 0);

  const apply = async () => {
    if (!data) return;
    setApplying(true);
    setError("");
    try {
      const patch: Record<string, unknown> = {};
      if (newTopics.length > 0) {
        const merged = [...assistant.topics];
        const seen = new Set(assistant.topics.map(normTopic));
        for (const topic of newTopics) {
          if (!seen.has(normTopic(topic))) {
            merged.push(topic);
            seen.add(normTopic(topic));
          }
        }
        patch.topics = merged;
      }
      if (addNotation && data.notation_notes.trim()) {
        const nuances = [...assistant.nuances];
        if (!nuances.includes(data.notation_notes.trim())) nuances.push(data.notation_notes.trim());
        patch.nuances = nuances;
      }
      if (useDescription && data.summary.trim()) patch.description = data.summary.trim();
      if (Object.keys(patch).length > 0) await assistantsApi.update(assistant.id, patch);

      for (const sheet of selectedSheets) {
        await sheetsApi.create(assistant.id, {
          title: sheet.title,
          kind: sheet.kind,
          description: sheet.description,
          content_markdown: sheet.content_markdown,
          source_document_id: document.id,
          visibility: document.visibility,
          is_canonical: true,
        });
      }
      if (selectedSheets.length > 0) onSheetsChanged();
      await kbApi.markAnalysisApplied(assistant.id, document.id);
      await onProfileChanged();
      await onApplied();
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setApplying(false);
    }
  };

  return (
    <Modal title={`Разбор: ${document.title}`} open onClose={onClose} wide>
      <div className="space-y-5">
        <ErrorNote message={error} />
        {data === null && !error && (
          <Spinner label="Анализируем документ — извлекаем темы, справочные данные и нотацию курса. Для больших файлов это может занять несколько минут…" />
        )}
        {data && (
          <>
            {document.visibility === "quarantine" && (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                Источник находится в карантине. Темы и описание не выбраны автоматически; применяйте только проверенные пункты.
              </div>
            )}
            {data.summary && (
              <div className="rounded-md border border-border bg-muted/30 p-3 text-sm">
                <p className="text-xs font-semibold uppercase text-muted-foreground mb-1">О курсе</p>
                <MathText>{data.summary}</MathText>
                {!assistant.description.trim() && (
                  <label className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={useDescription}
                      onChange={(e) => setUseDescription(e.target.checked)}
                      className="h-4 w-4 accent-accent"
                    />
                    Использовать как описание дисциплины
                  </label>
                )}
              </div>
            )}

            <section className="space-y-2">
              <p className="text-sm font-semibold">
                Темы курса{" "}
                <span className="text-xs font-normal text-muted-foreground">
                  · выбрано {newTopics.length} из {(data.topics ?? []).filter((t) => !existing.has(normTopic(t))).length} новых
                </span>
              </p>
              {data.topics.length === 0 ? (
                <p className="text-xs text-muted-foreground">В этом документе не нашлось явной программы тем.</p>
              ) : (
                <div className="space-y-1 max-h-52 overflow-y-auto rounded-md border border-border p-2">
                  {data.topics.map((topic, i) => {
                    const inProfile = existing.has(normTopic(topic));
                    return (
                      <label key={i} className="flex items-start gap-2 text-sm py-0.5">
                        <input
                          type="checkbox"
                          className="mt-1 accent-accent"
                          checked={inProfile || topicSel.has(topic)}
                          disabled={inProfile}
                          onChange={() => toggleTopic(topic)}
                        />
                        <span className={inProfile ? "text-muted-foreground" : ""}>{topic}</span>
                        {inProfile && <Badge className="shrink-0">уже есть</Badge>}
                      </label>
                    );
                  })}
                </div>
              )}
            </section>

            <section className="space-y-2">
              <p className="text-sm font-semibold">
                Справочные материалы{" "}
                <span className="text-xs font-normal text-muted-foreground">
                  · данные, обозначения и формулы из документа — ассистент будет использовать именно их
                </span>
              </p>
              {data.sheets.length === 0 ? (
                <p className="text-xs text-muted-foreground">Справочных данных в документе не обнаружено.</p>
              ) : (
                <div className="space-y-2">
                  {data.sheets.map((sheet, i) => (
                    <div key={i} className="rounded-md border border-border">
                      <div className="flex items-start gap-2 p-2.5">
                        <input
                          type="checkbox"
                          className="mt-1 accent-accent"
                          checked={sheetSel.has(i)}
                          onChange={() => toggleSheet(i)}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-medium">{sheet.title}</span>
                            <Badge tone="info">{SHEET_KIND_LABELS[sheet.kind] ?? sheet.kind}</Badge>
                          </div>
                          {sheet.description && (
                            <p className="text-xs text-muted-foreground mt-0.5">{sheet.description}</p>
                          )}
                          <button
                            className="mt-1 text-xs text-accent hover:underline"
                            onClick={() => toggleExpand(i)}
                          >
                            {expanded.has(i) ? "свернуть" : "показать содержимое"}
                          </button>
                          {expanded.has(i) && (
                            <div className="mt-2 max-h-64 overflow-y-auto rounded border border-border bg-muted/20 p-2 text-sm">
                              <MathText>{sheet.content_markdown}</MathText>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {data.notation_notes.trim() && (
              <section className="space-y-1.5">
                <label className="flex items-center gap-2 text-sm font-semibold">
                  <input
                    type="checkbox"
                    checked={addNotation}
                    onChange={(e) => setAddNotation(e.target.checked)}
                    className="h-4 w-4 accent-accent"
                  />
                  Нотация и терминология курса → в нюансы ассистента
                </label>
                <div className="rounded-md border border-border bg-muted/20 p-2 text-sm text-muted-foreground">
                  <MathText>{data.notation_notes}</MathText>
                </div>
              </section>
            )}

            <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
              <Button
                variant="ghost"
                className="text-xs"
                title="Запустить разбор заново (несколько минут)"
                onClick={() => load(true)}
              >
                <RefreshCw className="h-3.5 w-3.5" /> Обновить разбор
              </Button>
              <div className="flex gap-2">
                <Button variant="ghost" onClick={onClose}>
                  Отмена
                </Button>
                <Button onClick={apply} loading={applying} disabled={totalToApply === 0}>
                  Применить ({totalToApply})
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

function SheetsSection({ assistant, refreshKey }: { assistant: Assistant; refreshKey: number }) {
  const [sheets, setSheets] = useState<ReferenceSheet[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<ReferenceSheetKind | "all">("all");
  const [showAll, setShowAll] = useState(false);
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
  }, [assistant.id, refreshKey]);

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

  const filteredSheets = useMemo(() => {
    if (sheets === null) return [];
    const needle = query.trim().toLocaleLowerCase("ru-RU");
    return sheets.filter((sheet) => {
      if (kind !== "all" && sheet.kind !== kind) return false;
      if (!needle) return true;
      return `${sheet.title}\n${sheet.description}\n${sheet.content_markdown}`
        .toLocaleLowerCase("ru-RU")
        .includes(needle);
    });
  }, [kind, query, sheets]);

  const visibleSheets = showAll ? filteredSheets : filteredSheets.slice(0, 12);

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[16rem] flex-1">
          <h2 className="text-sm font-semibold">Справочные материалы курса</h2>
          <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
            Заполняются автоматически кнопкой «Разобрать». Ассистент обязан использовать именно эти значения констант,
            обозначения и терминологию вместо общесправочных. При необходимости поправьте или добавьте вручную.
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button variant="ghost" onClick={() => setFromDocOpen(true)}>
            <Plus className="h-4 w-4" /> Из фрагментов
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

      {sheets !== null && sheets.length > 0 && (
        <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3 sm:flex-row sm:items-center">
          <Input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setShowAll(false);
            }}
            placeholder="Найти таблицу, формулу или обозначение"
            aria-label="Поиск по справочным материалам"
            className="sm:flex-1"
          />
          <Select
            value={kind}
            onChange={(event) => {
              setKind(event.target.value as ReferenceSheetKind | "all");
              setShowAll(false);
            }}
            aria-label="Тип справочного материала"
            className="sm:w-48"
          >
            <option value="all">Все типы</option>
            {Object.entries(SHEET_KIND_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
          <span className="shrink-0 text-xs text-muted-foreground">
            {filteredSheets.length} из {sheets.length}
          </span>
        </div>
      )}

      <ErrorNote message={error} />
      {sheets === null ? (
        <Spinner />
      ) : sheets.length === 0 ? (
        <EmptyState
          title="Справочных материалов пока нет"
          hint="Добавьте таблицы констант, глоссарий и обозначения курса — вручную или из фрагментов документа"
        />
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            {visibleSheets.map((sheet) => (
              <Card key={sheet.id} className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="text-sm font-medium truncate">{sheet.title}</p>
                      <Badge tone="info">{SHEET_KIND_LABELS[sheet.kind]}</Badge>
                      {sheet.is_canonical && <Badge tone="accent">канон</Badge>}
                      {sheet.visibility !== "student" && (
                        <Badge tone={sheet.visibility === "quarantine" ? "destructive" : "warning"}>
                          {VISIBILITY_LABELS[sheet.visibility]}
                        </Badge>
                      )}
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
          {filteredSheets.length === 0 && (
            <EmptyState title="Ничего не найдено" hint="Измените запрос или выберите другой тип материала" />
          )}
          {filteredSheets.length > 12 && (
            <div className="flex justify-center">
              <Button variant="secondary" onClick={() => setShowAll((value) => !value)}>
                {showAll ? "Свернуть список" : `Показать ещё ${filteredSheets.length - 12}`}
              </Button>
            </div>
          )}
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
  const [visibility, setVisibility] = useState<MaterialVisibility>(sheet?.visibility ?? "student");
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
        visibility,
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
          <Textarea rows={14} value={content} onChange={(e) => setContent(e.target.value)} className="font-mono" />
        </Field>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_18rem] sm:items-end">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={isCanonical} onChange={(e) => setIsCanonical(e.target.checked)} />
            Канонический источник — включается в промпты по умолчанию
          </label>
          <Field label="Кому доступен справочник">
            <Select value={visibility} onChange={(event) => setVisibility(event.target.value as MaterialVisibility)}>
              {Object.entries(VISIBILITY_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </Select>
          </Field>
        </div>
        {visibility !== "student" && (
          <p className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            Этот справочник не попадёт в student tutor и публикацию курса в Picrete.
          </p>
        )}
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
