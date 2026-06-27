import { Routes, Route } from "react-router-dom";
import Login from "./routes/Login";
import Chat from "./routes/Chat";
import History from "./routes/History";
import Preferences from "./routes/Preferences";
import AdminDocuments from "./routes/AdminDocuments";
import { RequireAuth } from "./lib/auth/RequireAuth";
import { RequireRole } from "./lib/auth/RequireRole";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Chat />
          </RequireAuth>
        }
      />
      <Route
        path="/history"
        element={
          <RequireAuth>
            <History />
          </RequireAuth>
        }
      />
      <Route
        path="/preferences"
        element={
          <RequireAuth>
            <Preferences />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/documents"
        element={
          <RequireAuth>
            <RequireRole role="hr">
              <AdminDocuments />
            </RequireRole>
          </RequireAuth>
        }
      />
    </Routes>
  );
}
