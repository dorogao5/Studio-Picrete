import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { AppProvider } from "./lib/context";
import AssistantDetail from "./pages/AssistantDetail";
import Disciplines from "./pages/Disciplines";
import Login from "./pages/Login";
import Playground from "./pages/Playground";
import Providers from "./pages/Providers";
import UsersPage from "./pages/Users";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          element={
            <AppProvider>
              <Layout />
            </AppProvider>
          }
        >
          <Route path="/" element={<Navigate to="/disciplines" replace />} />
          <Route path="/disciplines" element={<Disciplines />} />
          <Route path="/disciplines/:id" element={<AssistantDetail />} />
          <Route path="/providers" element={<Providers />} />
          <Route path="/playground" element={<Playground />} />
          <Route path="/users" element={<UsersPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/disciplines" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
