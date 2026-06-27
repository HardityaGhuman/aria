import { Routes, Route } from "react-router-dom";
import Login from "./routes/Login";
import Chat from "./routes/Chat";
import History from "./routes/History";
import { ComingSoon } from "./routes/ComingSoon";
import { RequireAuth } from "./lib/auth/RequireAuth";

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
            <ComingSoon
              title="Preferences"
              blurb="Tone, length, and language controls for Aria's answers. Landing in the next build."
            />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/documents"
        element={
          <RequireAuth>
            <ComingSoon
              title="HR Documents"
              blurb="Upload, reindex, and manage the policy knowledge base. HR-only. Landing in the next build."
            />
          </RequireAuth>
        }
      />
    </Routes>
  );
}
