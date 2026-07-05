import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiErrorMessage, authApi } from "../lib/api";
import { Button, Card, ErrorNote, Field, Input } from "../components/ui";

export default function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const token = await authApi.login(username, password);
      localStorage.setItem("studio_token", token);
      navigate("/assistants");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-sm p-6">
        <h1 className="text-lg font-semibold">Picrete Studio</h1>
        <p className="text-sm text-muted-foreground mt-1 mb-5">Платформа создания ИИ-ассистентов для преподавателей</p>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Логин">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" />
          </Field>
          <Field label="Пароль">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </Field>
          <ErrorNote message={error} />
          <Button type="submit" loading={loading} className="w-full">
            Войти
          </Button>
        </form>
      </Card>
    </div>
  );
}
