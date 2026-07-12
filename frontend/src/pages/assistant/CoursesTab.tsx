import { GraduationCap, Link2, Pencil, Plus, Send, Trash2 } from "lucide-react";
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
import type { Assistant, Course, PicreteCourseOption } from "../../lib/types";

export default function CoursesTab({ assistant }: { assistant: Assistant }) {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [picreteCourses, setPicreteCourses] = useState<PicreteCourseOption[]>([]);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Course | null | undefined>(undefined);
  const [publishingId, setPublishingId] = useState("");
  const [publishedId, setPublishedId] = useState("");

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

  const publish = async (course: Course) => {
    setPublishingId(course.id);
    setPublishedId("");
    setError("");
    try {
      await coursesApi.publish(assistant.id, course.id);
      setPublishedId(course.id);
    } catch (err) {
      setError(apiErrorMessage(err));
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
                  onClick={() => void publish(course)}
                >
                  <Send className="h-3.5 w-3.5" />
                  {publishedId === course.id ? "Опубликовано" : "Опубликовать в Picrete"}
                </Button>
                {!course.external_course_id && (
                  <p className="mt-1.5 text-center text-xs text-muted-foreground">Сначала выберите курс Picrete</p>
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
