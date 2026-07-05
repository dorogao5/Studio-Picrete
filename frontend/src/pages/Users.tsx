import { KeyRound, Plus, Shield, Trash2, UserCheck, UserX } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, Select, Spinner } from "../components/ui";
import { apiErrorMessage, usersApi } from "../lib/api";
import { useApp } from "../lib/context";
import type { UserOut } from "../lib/types";

export default function UsersPage() {
  const { me } = useApp();
  const [users, setUsers] = useState<UserOut[] | null>(null);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [created, setCreated] = useState<{ username: string; password: string } | null>(null);

  const reload = async () => {
    try {
      setUsers(await usersApi.list());
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  if (me && me.role !== "admin") {
    return <EmptyState title="Нет доступа" hint="Управление доступами доступно только администраторам" />;
  }

  const resetPassword = async (user: UserOut) => {
    const pw = prompt(`Новый пароль для «${user.username}» (минимум 6 символов):`);
    if (!pw) return;
    if (pw.length < 6) {
      alert("Пароль слишком короткий");
      return;
    }
    try {
      await usersApi.update(user.id, { password: pw });
      alert("Пароль обновлён");
    } catch (err) {
      alert(apiErrorMessage(err));
    }
  };

  return (
    <div className="max-w-3xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Доступы</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Аккаунты преподавателей. Все они работают в одном общем воркспейсе.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Выдать доступ
        </Button>
      </div>

      {created && (
        <Card className="p-4 border-success/40 bg-success/5">
          <p className="text-sm font-medium text-success">Доступ создан — передайте преподавателю:</p>
          <p className="mt-1 font-mono text-sm">
            логин: <b>{created.username}</b> · пароль: <b>{created.password}</b>
          </p>
          <p className="text-xs text-muted-foreground mt-1">Пароль показывается один раз. Он сможет сменить его сам.</p>
          <Button variant="ghost" className="mt-2" onClick={() => setCreated(null)}>
            Скрыть
          </Button>
        </Card>
      )}

      <ErrorNote message={error} />
      {users === null ? (
        <Spinner />
      ) : (
        <div className="space-y-2">
          {users.map((user) => (
            <Card key={user.id} className="p-4 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="font-medium truncate">{user.full_name || user.username}</p>
                  {user.role === "admin" ? (
                    <Badge tone="accent">
                      <Shield className="h-3 w-3 mr-1" /> админ
                    </Badge>
                  ) : (
                    <Badge>преподаватель</Badge>
                  )}
                  {!user.is_active && <Badge tone="destructive">отключён</Badge>}
                </div>
                <p className="text-xs text-muted-foreground font-mono mt-0.5">{user.username}</p>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <Button variant="ghost" onClick={() => resetPassword(user)} title="Сбросить пароль">
                  <KeyRound className="h-4 w-4" />
                </Button>
                {user.id !== me?.id && (
                  <>
                    <Button
                      variant="secondary"
                      onClick={async () => {
                        await usersApi.update(user.id, { is_active: !user.is_active });
                        reload();
                      }}
                    >
                      {user.is_active ? <UserX className="h-4 w-4" /> : <UserCheck className="h-4 w-4" />}
                    </Button>
                    <Button
                      variant="destructive"
                      onClick={async () => {
                        if (confirm(`Удалить пользователя «${user.username}»?`)) {
                          await usersApi.remove(user.id);
                          reload();
                        }
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      <CreateUserModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(username, password) => {
          setCreated({ username, password });
          reload();
        }}
      />
    </div>
  );
}

function CreateUserModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (username: string, password: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState("teacher");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const genPassword = () => setPassword(Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 6));

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      await usersApi.create({ username: username.trim(), password, full_name: fullName.trim(), role });
      onCreated(username.trim(), password);
      onClose();
      setUsername("");
      setFullName("");
      setPassword("");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Выдать доступ преподавателю" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Логин">
          <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="ivanov" className="font-mono" />
        </Field>
        <Field label="ФИО">
          <Input value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Иванов Иван Иванович" />
        </Field>
        <Field label="Роль">
          <Select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="teacher">Преподаватель</option>
            <option value="admin">Администратор</option>
          </Select>
        </Field>
        <Field label="Пароль" hint="Минимум 6 символов. Можно сгенерировать.">
          <div className="flex gap-2">
            <Input value={password} onChange={(e) => setPassword(e.target.value)} className="font-mono" />
            <Button variant="secondary" onClick={genPassword} type="button">
              Сгенерировать
            </Button>
          </div>
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={submit} loading={loading} disabled={username.trim().length < 3 || password.length < 6}>
            Создать
          </Button>
        </div>
      </div>
    </Modal>
  );
}
