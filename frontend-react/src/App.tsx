import { Routes, Route } from "react-router-dom";
import Login from "./routes/Login";
import Chat from "./routes/Chat";
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
    </Routes>
  );
}
