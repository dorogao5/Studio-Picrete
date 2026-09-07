import { AlertTriangle, CheckCircle2, Eye, GraduationCap, Link2, Pencil, Plus, Send, Trash2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Input,
  Modal,
  Select,
  Spinner,
} from "../../components/ui";
import { apiErrorMessage, coursesApi } from "../../lib/api";
import type { Assistant, Course, CoursePublishPreflight, PicreteCourseOption } from "../../lib/types";

export default function CoursesTab({ assistant }: { assistant: Assistant }) {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [picreteCourses, setPicreteCourses] = useState<PicreteCourseOption[]>([]);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Course | null | undefined>(undefined);
  const [publishingId, setPublishingId] = useState("");
  const [publishedId, setPublishedId] = useState("");
  const [reviewCourse, setReviewCourse] = useState<Course | null>(null);

  const reload = async () => {
    try {
      setCourses(await coursesApi.list(assistant.id));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    void reload();
    coursesApi.picreteOptions().then(setPicreteCourses).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistant.id]);

  const publish = async (course: Course, reviewToken: string, acknowledgeWarnings: boolean) => {
    setPublishingId(course.id);
    setPublishedId("");
    setError("");
    try {
      await coursesApi.publish(assistant.id, course.id, {
        review_token: reviewToken,
        acknowledge_warnings: acknowledgeWarnings,
      });
      setPublishedId(course.id);
      await reload();
    } catch (err) {
      setError(apiErrorMessage(err));
      throw err;
    } finally {
      setPublishingId("");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">
          Привяжите поток к курсу Picrete и публикуйте проверенную версию ассистента. Студенты увидят
          активный промпт разбора и канонические справочники; эксперименты в Studio на курс не повлияют
          до следующей публикации.
        </p>
        <Button onClick={() => setEditing(null)}>
          <Plus className="h-4 w-4" /> Курс
        </Button>
      </div>

      <ErrorNote message={error} />
      {courses === null ? (
        <Spinner />
      ) : courses.length === 0 ? (
        <EmptyState title="Курсов пока нет" hint="Добавьте поток и выберите соответствующий курс Picrete" />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {courses.map((course) => (
            <Card key={course.id} className="p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <GraduationCap className="h-4 w-4 shrink-0 text-accent" />
                    <p className="truncate font-medium">{course.name}</p>
                  </div>
                  {course.term && <p className="mt-0.5 text-xs text-muted-foreground">{course.term}</p>}
                  {course.description && <p className="mt-1 text-xs text-muted-foreground">{course.description}</p>}
                  {course.external_course_id ? (
                    <Badge tone="info" className="mt-2 max-w-full">
                      <Link2 className="mr-1 h-3 w-3 shrink-0" />
                      <span className="truncate">
                        {picreteCourses.find((item) => item.id === course.external_course_id)?.title ??
                          `Picrete: ${course.external_course_id}`}
                      </span>
                    </Badge>
                  ) : (
                    <Badge className="mt-2">не привязан к Picrete</Badge>
                  )}
                </div>
                <div className="flex shrink-0 gap-1">
                  <button
                    type="button"
                    className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                    onClick={() => setEditing(course)}
                    aria-label={`Изменить курс ${course.name}`}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    onClick={async () => {
                      if (!window.confirm(`Удалить курс «${course.name}» из Studio?`)) return;
                      await coursesApi.remove(assistant.id, course.id);
                      void reload();
                    }}
                    aria-label={`Удалить курс ${course.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              <div className="mt-4 border-t pt-3">
                <Button
                  className="w-full"
                  variant={publishedId === course.id ? "secondary" : "primary"}
                  disabled={!course.external_course_id}
                  loading={publishingId === course.id}
                  onClick={() => setReviewCourse(course)}
                >
                  <Send className="h-3.5 w-3.5" />
                  {publishedId === course.id
                    ? "Опубликовано"
                    : course.published_at
                      ? "Опубликовать обновление"
                      : "Опубликовать в Picrete"}
                </Button>
                {!course.external_course_id && (
                  <p className="mt-1.5 text-center text-xs text-muted-foreground">Сначала выберите курс Picrete</p>
                )}
                {course.published_at && (
                  <p className="mt-1.5 text-center text-xs text-muted-foreground">
                    Последняя публикация: {new Date(course.published_at).toLocaleString("ru-RU", {
                      day: "numeric",
                      month: "long",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </p>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      {editing !== undefined && (
        <CourseModal
          open
          onClose={() => setEditing(undefined)}
          assistantId={assistant.id}
          course={editing}
          picreteCourses={picreteCourses}
          onSaved={reload}
        />
      )}
      {reviewCourse && (
        <PublishReviewModal
          assistant={assistant}
          course={reviewCourse}
          courseLabel={picreteCourses.find((item) => item.id === reviewCourse.external_course_id)?.title ?? reviewCourse.external_course_id}
          publishing={publishingId === reviewCourse.id}
          onClose={() => setReviewCourse(null)}
          onPublish={async (token, acknowledgeWarnings) => {
            await publish(reviewCourse, token, acknowledgeWarnings);
            setReviewCourse(null);
          }}
        />
      )}
    </div>
  );
}

function PublishReviewModal({
  assistant,
  course,
  courseLabel,
  publishing,
  onClose,
  onPublish,
}: {
  assistant: Assistant;
  course: Course;
  courseLabel: string;
  publishing: boolean;
  onClose: () => void;
  onPublish: (token: string, acknowledgeWarnings: boolean) => Promise<void>;
}) {
  const [preflight, setPreflight] = useState<CoursePublishPreflight | null>(null);
  const [loading, setLoading] = useState(true);
  const [acknowledgeWarnings, setAcknowledgeWarnings] = useState(false);
  const [error, setError] = useState("");

  const runPreflight = async () => {
    setLoading(true);
    setError("");
    setAcknowledgeWarnings(false);
    try {
      setPreflight(await coursesApi.preflight(assistant.id, course.id));
    } catch (err) {
      setError(apiErrorMessage(err));
      setPreflight(null);
    } finally {
      setLoading(false);
    }
  };

  const submitPublish = async () => {
    if (!preflight) return;
    setError("");
    try {
      await onPublish(preflight.review_token, acknowledgeWarnings);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    void runPreflight();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistant.id, course.id]);

  return (
    <Modal title="Review перед публикацией" open onClose={onClose} wide>
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/20 p-3.5">
          <Eye className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
          <div>
            <p className="text-sm font-semibold">Ничего ещё не опубликовано</p>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              Сначала Studio собирает неизменяемый снимок, показывает blockers/warnings и student preview.
              Кнопка публикации разблокируется только для просмотренной версии.
            </p>
          </div>
        </div>

        {loading ? <Spinner label="Проверяем снимок курса…" /> : null}
        <ErrorNote message={error} />
        {preflight && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className={`rounded-lg border p-3.5 ${preflight.blockers.length ? "border-destructive/35 bg-destructive/5" : "border-success/35 bg-success/5"}`}>
                <div className="flex items-center gap-2">
                  {preflight.blockers.length ? <XCircle className="h-4 w-4 text-destructive" /> : <CheckCircle2 className="h-4 w-4 text-success" />}
                  <p className="text-sm font-semibold">Blockers: {preflight.blockers.length}</p>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">Блокируют отправку снимка на курс.</p>
              </div>
              <div className={`rounded-lg border p-3.5 ${preflight.warnings.length ? "border-warning/35 bg-warning/5" : "border-border bg-card"}`}>
                <div className="flex items-center gap-2">
                  <AlertTriangle className={`h-4 w-4 ${preflight.warnings.length ? "text-warning" : "text-muted-foreground"}`} />
                  <p className="text-sm font-semibold">Warnings: {preflight.warnings.length}</p>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">Требуют осознанного подтверждения.</p>
              </div>
            </div>

            {[...preflight.blockers, ...preflight.warnings].length > 0 && (
              <ul className="space-y-2">
                {[...preflight.blockers, ...preflight.warnings].map((issue, index) => (
                  <li key={`${issue.code}-${index}`} className={`rounded-lg border p-3 text-xs ${issue.severity === "blocker" ? "border-destructive/30 bg-destructive/5" : "border-warning/30 bg-warning/5"}`}>
                    <p className="font-semibold text-foreground">{issue.title}</p>
                    <p className="mt-0.5 text-muted-foreground">{issue.message}</p>
                  </li>
                ))}
              </ul>
            )}

            {preflight.ok && (
              <article className="overflow-hidden rounded-xl border border-border bg-background shadow-soft">
                <header className="border-b border-border bg-card px-4 py-3 sm:px-5">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Student preview · {courseLabel}</p>
                  <h3 className="mt-1 text-lg font-semibold">{preflight.preview.assistant_name}</h3>
                  <p className="text-sm text-muted-foreground">{preflight.preview.discipline} · для {preflight.preview.audience}</p>
                </header>
                <div className="space-y-4 p-4 sm:p-5">
                  <p className="text-sm leading-6">{preflight.preview.description || "Описание не заполнено"}</p>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <PreviewFact label="Промпт разбора" value={`v${preflight.preview.tutor_prompt_version}`} />
                    <PreviewFact label="Модель" value={preflight.preview.model_id} />
                    <PreviewFact label="Справочники" value={String(preflight.preview.reference_sheets.length)} />
                  </div>
                  {preflight.preview.topics.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {preflight.preview.topics.slice(0, 8).map((topic) => <Badge key={topic}>{topic}</Badge>)}
                    </div>
                  )}
                </div>
              </article>
            )}

            {preflight.warnings.length > 0 && preflight.ok && (
              <label className="flex min-h-11 items-start gap-2 rounded-lg border border-warning/35 bg-warning/5 p-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-accent"
                  checked={acknowledgeWarnings}
                  onChange={(event) => setAcknowledgeWarnings(event.target.checked)}
                />
                <span>Я просмотрел(а) предупреждения и student preview этой версии.</span>
              </label>
            )}
          </>
        )}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="ghost" onClick={onClose}>Отмена</Button>
          <Button variant="secondary" onClick={() => void runPreflight()} loading={loading}>Проверить заново</Button>
          <Button
            onClick={() => void submitPublish()}
            loading={publishing}
            disabled={!preflight?.ok || !preflight.review_token || (preflight.warnings.length > 0 && !acknowledgeWarnings)}
          >
            <Send className="h-4 w-4" /> Опубликовать просмотренную версию
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function PreviewFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-1 break-words text-sm font-medium">{value}</p>
    </div>
  );
}

function CourseModal({
  open,
  onClose,
  assistantId,
  course,
  picreteCourses,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  assistantId: string;
  course: Course | null;
  picreteCourses: PicreteCourseOption[];
  onSaved: () => void;
}) {
  const [name, setName] = useState(course?.name ?? "");
  const [term, setTerm] = useState(course?.term ?? "");
  const [description, setDescription] = useState(course?.description ?? "");
  const [externalId, setExternalId] = useState(course?.external_course_id ?? "");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      const body = { name, term, description, external_course_id: externalId };
      if (course) await coursesApi.update(assistantId, course.id, body);
      else await coursesApi.create(assistantId, body);
      onSaved();
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title={course ? "Настроить курс" : "Новый курс"} open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Название потока">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="напр. ХИМ-101, поток А" />
        </Field>
        <Field label="Семестр / период">
          <Input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="осень 2026" />
        </Field>
        <Field label="Описание">
          <Input value={description} onChange={(event) => setDescription(event.target.value)} />
        </Field>
        <Field label="Курс в Picrete" hint="Публикация заменит только снимок ассистента в выбранном курсе">
          {picreteCourses.length > 0 ? (
            <Select value={externalId} onChange={(event) => setExternalId(event.target.value)}>
              <option value="">Не привязывать</option>
              {picreteCourses.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}{item.organization ? ` · ${item.organization}` : ""}
                </option>
              ))}
            </Select>
          ) : (
            <Input
              value={externalId}
              onChange={(event) => setExternalId(event.target.value)}
              className="font-mono"
              placeholder="ID курса Picrete"
            />
          )}
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Отмена</Button>
          <Button onClick={submit} loading={loading} disabled={!name.trim()}>Сохранить</Button>
        </div>
      </div>
    </Modal>
  );
}
