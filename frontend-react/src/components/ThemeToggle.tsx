import { useState } from "react";

// The <html> class (set in main.tsx before paint) is the single source of truth.
// The toggle just flips it + persists — no re-deriving from matchMedia, which
// would otherwise disagree with the initial value under StrictMode remounts.
export function ThemeToggle() {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );

  function toggle() {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
    setDark(next);
  }

  return (
    <button
      onClick={toggle}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Light mode" : "Dark mode"}
      className="rounded-md px-2 py-1 text-text-secondary hover:bg-canvas hover:text-text-ink"
    >
      {dark ? "☀" : "☾"}
    </button>
  );
}
