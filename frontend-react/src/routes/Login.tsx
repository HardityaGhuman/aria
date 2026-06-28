import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth/AuthContext";
import { Button } from "../components/Button";
import { AnimatedBackground } from "../components/AnimatedBackground";
import { ApiError } from "../lib/api/client";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  const inputClass =
    "w-full rounded-lg border border-hairline bg-surface px-3 py-2.5 text-[15px] outline-none focus:border-ink";

  return (
    <div className="relative flex h-full items-center justify-center bg-canvas">
      <AnimatedBackground />
      <div className="relative w-[360px] rounded-card border border-hairline bg-surface p-8 shadow-card">
        <div className="mb-1 text-center text-[28px] font-bold tracking-tight">
          Aria<span className="text-marigold">.</span>
        </div>
        <div className="mb-6 text-center text-[14px] text-text-secondary">
          Sign in to your assistant
        </div>

        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div>
            <label className="mb-1 block text-[13px] text-text-secondary">Email</label>
            <input
              className={inputClass}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@company.com"
              required
            />
          </div>
          <div>
            <div className="mb-1 flex justify-between text-[13px] text-text-secondary">
              <label>Password</label>
              <span className="text-text-tertiary">Forgot?</span>
            </div>
            <input
              className={inputClass}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && <div className="text-[13px] text-badge-hr">{error}</div>}

          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Signing in…" : "Sign In"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="w-full"
            disabled
            title="SSO is not available yet"
          >
            Single Sign-On
          </Button>
        </form>

        <div className="mt-6 text-center text-[12px] text-text-tertiary">
          Internal access only. Secured by Aria Identity.
        </div>
      </div>
    </div>
  );
}
