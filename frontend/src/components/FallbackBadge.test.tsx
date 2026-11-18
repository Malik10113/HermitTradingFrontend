import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FallbackBadge, ProfileBadge } from "./ui";
import { fallbackWarning } from "../lib/execution";

describe("execution fallback visibility", () => {
  it("renders a warning badge when Book resolves to Conservative", () => {
    render(
      <FallbackBadge
        requested="book"
        effective="conservative"
        reason="no_book_coverage"
      />,
    );
    const badge = screen.getByTestId("fallback-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute("role", "note");
    expect(badge.textContent).toContain("Book");
    expect(badge.textContent).toContain("Conservative");
    expect(badge.textContent).toContain("no book coverage");
  });

  it("renders nothing when the requested profile is effective", () => {
    const { container } = render(
      <FallbackBadge requested="conservative" effective="conservative" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("carries the profile label in the full warning sentence", () => {
    expect(fallbackWarning("book", "conservative", "no_book_coverage")).toContain(
      "Requested Book, effective Conservative",
    );
    expect(fallbackWarning("book", "book", null)).toBeNull();
  });

  it("always labels net metrics with the effective profile", () => {
    render(<ProfileBadge profile="stress" prefix="Net" />);
    expect(screen.getByText("Net · Stress")).toBeInTheDocument();
  });
});
