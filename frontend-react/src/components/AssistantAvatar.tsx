// Aria's mark: a marigold four-point spark on ink — echoes the "Aria." accent
// dot and reads like a small sunburst/Claude-style glyph.
export function AssistantAvatar() {
  return (
    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ink">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
        {/* The spark's points span y 2–18 (visual center y=10), so it sits 2px
            high in a 24px box — nudge down 2 to truly center it in the circle. */}
        <path
          d="M12 2c.6 4.3 3.7 7.4 8 8-4.3.6-7.4 3.7-8 8-.6-4.3-3.7-7.4-8-8 4.3-.6 7.4-3.7 8-8z"
          fill="#F0A23C"
          transform="translate(0 2)"
        />
      </svg>
    </div>
  );
}
