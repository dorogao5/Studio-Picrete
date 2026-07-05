import { GraduationCap, Link2, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Spinner } from "../../components/ui";
import { apiErrorMessage, coursesApi } from "../../lib/api";
import type { Assistant, Course } from "../../lib/types";

export default function CoursesTab({ assistant }: { assistant: Assistant }) {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  const reload = async () => {
    try {
      setCourses(await coursesApi.list(assistant.id));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistant.id]);

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-muted-foreground max-w-2xl">
          Курсы этой дисциплины — конкретные потоки/семестры, где применяется ассистент. Поле «ID курса в Picrete»
          — задел под привязку к курсам основной платформы (проверка ДЗ и контрольных будет использовать этого
          ассистента).
        </p>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Курс
        </Button>
      </div>

      <ErrorNote message={error} />
      {courses === null ? (
        <Spinner />
      ) : courses.length === 0 ? (
        <EmptyState title="Курсов пока нет" hint="Добавьте поток, к которому потом привяжете курс из Picrete" />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {courses.map((course) => (
            <Card key={course.id} className="p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <GraduationCap className="h-4 w-4 text-accent shrink-0" />
                    <p className="font-medium truncate">{course.name}</p>
                  </div>
                  {course.term && <p className="text-xs text-muted-foreground mt-0.5">{course.term}</p>}
                  {course.description && <p className="text-xs text-muted-foreground mt-1">{course.description}</p>}
                  {course.external_course_id ? (
                    <Badge tone="info" className="mt-2">
                      <Link2 className="h-3 w-3 mr-1" /> Picrete: {course.external_course_id}
                    </Badge>
                  ) : (
                    <Badge className="mt-2">не привязан к Picrete</Badge>
                  )}
                </div>
                <button
                  className="p-1 text-muted-foreground hover:text-destructive shrink-0"
                  onClick={async () => {
                    await coursesApi.remove(assistant.id, course.id);
                    reload();
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <CreateCourseModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        assistantId={assistant.id}
        onCreated={reload}
      />
    </div>
  );
}

function CreateCourseModal({
  open,
  onClose,
  assistantId,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  assistantId: string;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [term, setTerm] = useState("");
  const [description, setDescription] = useState("");
  const [externalId, setExternalId] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      await coursesApi.create(assistantId, {
        name,
        term,
        description,
        external_course_id: externalId,
      });
      onCreated();
      onClose();
      setName("");
      setTerm("");
      setDescription("");
      setExternalId("");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Новый курс" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Название">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="напр. ХИМ-101, поток А" />
        </Field>
        <Field label="Семестр / период">
          <Input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="осень 2026" />
        </Field>
        <Field label="Описание">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="ID курса в Picrete (необязательно)" hint="Заполнится при привязке к основной платформе">
          <Input value={externalId} onChange={(e) => setExternalId(e.target.value)} className="font-mono" />
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={!name.trim()}>
            Создать
          </Button>
        </div>
      </div>
    </Modal>
  );
}
