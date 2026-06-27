import { useNavigate } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { HistoryList } from "../components/HistoryList";

export default function History() {
  const navigate = useNavigate();
  return (
    <AppShell>
      <div className="py-2">
        <h1 className="mb-1 text-[24px] font-semibold tracking-tight">History</h1>
        <p className="mb-6 text-[14px] text-text-secondary">Your past conversations with Aria.</p>
        <div className="rounded-card border border-hairline bg-surface p-2">
          <HistoryList onOpen={(id) => navigate(`/?s=${id}`)} />
        </div>
      </div>
    </AppShell>
  );
}
