import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { AppProvider } from "./lib/context";

const AssistantDetail = lazy(() => import("./pages/AssistantDetail"));
const Disciplines = lazy(() => import("./pages/Disciplines"));
const Login = lazy(() => import("./pages/Login"));
const Playground = lazy(() => import("./pages/Playground"));
const Providers = lazy(() => import("./pages/Providers"));
const UsersPage = lazy(() => import("./pages/Users"));

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<div className="flex min-h-screen items-center justify-center"><Spinner label="Открываем раздел" /></div>}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<ProtectedWorkspace />}>
            <Route path="/" element={<Navigate to="/disciplines" replace />} />
            <Route path="/disciplines" element={<Disciplines />} />
            <Route path="/disciplines/:id" element={<AssistantDetail />} />
            <Route path="/providers" element={<Providers />} />
            <Route path="/playground" element={<Playground />} />
            <Route path="/users" element={<UsersPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/disciplines" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}

function ProtectedWorkspace() {
  if (!localStorage.getItem("studio_token")) {
    return <Navigate to="/login" replace />;
  }

  return (
    <AppProvider>
      <Layout />
    </AppProvider>
  );
}
