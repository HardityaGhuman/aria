import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { RequireRole } from "./RequireRole";
import * as auth from "./AuthContext";

function renderAs(role: string) {
  vi.spyOn(auth, "useAuth").mockReturnValue({
    user: { id: 1, role, region: "us" },
    ready: true,
    login: vi.fn(),
    logout: vi.fn(),
  } as any);
  render(
    <MemoryRouter>
      <RequireRole role="hr">
        <div>secret</div>
      </RequireRole>
    </MemoryRouter>,
  );
}

it("hides content from a non-hr role", () => {
  renderAs("employee");
  expect(screen.queryByText("secret")).toBeNull();
});
it("shows content to hr", () => {
  renderAs("hr");
  expect(screen.getByText("secret")).toBeInTheDocument();
});
