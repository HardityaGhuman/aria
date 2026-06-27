import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./lib/auth/AuthContext";
import App from "./App";
import "./styles/theme.css";

// Apply the saved theme before first paint to avoid a flash.
{
  const saved = localStorage.getItem("theme");
  const dark = saved
    ? saved === "dark"
    : (window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  document.documentElement.classList.toggle("dark", dark);
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, staleTime: 30_000 },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
