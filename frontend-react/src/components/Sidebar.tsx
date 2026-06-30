import { NavLink, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../lib/auth/AuthContext";
import { Button } from "./Button";
import { HistoryList } from "./HistoryList";
import { ThemeToggle } from "./ThemeToggle";

const itemClass = (active: boolean) =>
  `flex items-center gap-3 rounded-lg px-3 py-2 text-[15px] ${
    active ? "bg-canvas font-medium text-text-ink" : "text-text-secondary hover:bg-canvas"
  }`;

function LogoutIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const activeSession = params.get("s");

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
          Chats
        </div>
        <HistoryList
          activeId={activeSession}
          onOpen={(id) => navigate(`/?s=${id}`)}
          onActiveDeleted={() => navigate("/")}
        />
      </div>

      {/* Persistent account bar. */}
      <div className="mt-2 flex items-center gap-2 rounded-lg border border-hairline bg-surface px-2 py-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-ink text-[12px] font-semibold uppercase text-on-ink">
          {(user?.email ?? user?.role ?? "u")[0]}
        </div>
        <div className="min-w-0 flex-1 leading-tight">
          <div className="truncate text-[13px] text-text-ink">{user?.email ?? "Account"}</div>
          <div className="text-[10px] uppercase tracking-wide text-text-tertiary">
            {user?.role} · {user?.region}
          </div>
        </div>
        <ThemeToggle />
        <button
          onClick={() => logout().then(() => navigate("/login"))}
          title="Sign out"
          aria-label="Sign out"
          className="rounded-md px-2 py-1 text-text-secondary hover:bg-canvas hover:text-badge-hr"
        >
          <LogoutIcon />
        </button>
      </div>
    </aside>
  );
}
