import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth/AuthContext";
import { Button } from "./Button";
import { HistoryList } from "./HistoryList";

const itemClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-3 rounded-lg px-3 py-2 text-[15px] ${
    isActive ? "bg-canvas text-text-ink font-medium" : "text-text-secondary hover:bg-canvas"
  }`;

export function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <aside className="flex h-full w-[260px] flex-col border-r border-hairline bg-surface px-3 py-4">
      <div className="px-2 pb-4">
        <div className="text-[20px] font-semibold tracking-tight">
          Aria<span className="text-marigold">.</span>
        </div>
        <div className="text-[12px] text-text-tertiary">Internal Assistant</div>
      </div>

      <NavLink to="/" end>
        <Button className="mb-4 w-full">+ New chat</Button>
      </NavLink>

      <nav className="flex flex-col gap-1">
        <NavLink to="/" end className={itemClass}>
          New chat
        </NavLink>
        <NavLink to="/history" className={itemClass}>
          History
        </NavLink>
        <NavLink to="/preferences" className={itemClass}>
          Preferences
        </NavLink>
        {user?.role === "hr" && (
          <NavLink to="/admin/documents" className={itemClass}>
            HR Documents
          </NavLink>
        )}
      </nav>

      <div className="mt-4 flex-1 overflow-y-auto border-t border-hairline pt-3">
        <div className="px-3 pb-1 text-[12px] uppercase tracking-wide text-text-tertiary">
          History
        </div>
        <HistoryList onOpen={(id) => navigate(`/?s=${id}`)} />
      </div>

      <button
        onClick={() => logout().then(() => navigate("/login"))}
        className="mt-2 px-3 py-2 text-left text-[13px] text-text-tertiary hover:text-text-secondary"
      >
        Sign out
      </button>
    </aside>
  );
}
