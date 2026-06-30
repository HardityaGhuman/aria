import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { Sidebar } from "./Sidebar";
import * as auth from "../lib/auth/AuthContext";

function renderAs(role: string) {
  vi.spyOn(auth, "useAuth").mockReturnValue({
    user: { id: 1, role, region: "us" },
    ready: true,
    login: vi.fn(),
    logout: vi.fn(),
  } as any);
  const qc = new QueryClient();
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("hides HR Documents for an employee", () => {
  renderAs("employee");
  expect(screen.queryByText("HR Documents")).toBeNull();
});

it("shows HR Documents for hr", () => {
  renderAs("hr");
  expect(screen.getByText("HR Documents")).toBeInTheDocument();
});
