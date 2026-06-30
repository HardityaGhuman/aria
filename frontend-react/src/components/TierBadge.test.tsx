import { render, screen } from "@testing-library/react";
import { TierBadge } from "./TierBadge";

it("shows a lock glyph only for hr_only", () => {
  const { rerender } = render(<TierBadge tier="all" />);
  expect(screen.getByText(/all/i)).toBeInTheDocument();
  expect(screen.queryByLabelText("locked")).toBeNull();
  rerender(<TierBadge tier="hr_only" />);
  expect(screen.getByLabelText("locked")).toBeInTheDocument();
});
