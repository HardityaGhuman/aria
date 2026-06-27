import { NavLink, useNavigate, useSearchParams, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth/AuthContext";
import { Button } from "./Button";
import { HistoryList } from "./HistoryList";

const itemClass = (active: boolean) =>
  `flex items-center gap-3 rounded-lg px-3 py-2 text-[15px] ${
    active ? "bg-canvas font-medium text-text-ink" : "text-text-secondary hover:bg-canvas"
  }`;

export function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const activeSession = params.get("s");

  // "New chat" is active only on a fresh "/" (no open session).
  const onNewChat = location.pathname === "/" && !activeSession;

  return (
    <aside className="flex h-full w-[260px] flex-col border-r border-hairline bg-surface px-3 py-4">
      <div className="px-2 pb-4">
        <div className="text-[20px] font-semibold tracking-tight">
          Aria<span className="text-marigold">.</span>
        </div>
        <div className="text-[12px] text-text-tertiary">Internal Assistant</div>
      </div>

      <Button className="mb-4 w-full" onClick={() => navigate("/")}>
        + New chat
      </Button>

      <nav className="flex flex-col gap-1">
        <button className={itemClass(onNewChat)} onClick={() => navigate("/")}>
          New chat
        </button>
        <NavLink to="/history" className={({ isActive }) => itemClass(isActive)}>
          History
        </NavLink>
        <NavLink to="/preferences" className={({ isActive }) => itemClass(isActive)}>
          Preferences
        </NavLink>
        {user?.role === "hr" && (
          <NavLink to="/admin/documents" className={({ isActive }) => itemClass(isActive)}>
            HR Documents
          </NavLink>
        )}
      </nav>

      <div className="mt-4 flex-1 overflow-y-auto border-t border-hairline pt-3">
        <div className="px-3 pb-1 text-[12px] uppercase tracking-wide text-text-tertiary">
          History
        </div>
        <HistoryList activeId={activeSession} onOpen={(id) => navigate(`/?s=${id}`)} />
      </div>

      {/* Persistent account bar (ChatGPT-style). */}
      <div className="mt-2 flex items-center gap-2 rounded-lg border border-hairline bg-surface px-2 py-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-ink text-[12px] font-semibold uppercase text-white">
          {(user?.role ?? "u")[0]}
        </div>
        <div className="min-w-0 flex-1 leading-tight">
          <div className="truncate text-[13px] capitalize text-text-ink">{user?.role ?? "User"}</div>
          <div className="text-[10px] uppercase tracking-wide text-text-tertiary">
            {user?.region}
          </div>
        </div>
        <button
          onClick={() => logout().then(() => navigate("/login"))}
          title="Sign out"
          aria-label="Sign out"
          className="rounded-md px-2 py-1 text-text-secondary hover:bg-canvas hover:text-text-ink"
        >
          ⎋
        </button>
      </div>
    </aside>
  );
}
