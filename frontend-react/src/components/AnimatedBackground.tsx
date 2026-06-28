// Ambient, premium backdrop: large blurred neutral-silver radial glows drifting
// slowly, with one faint marigold accent. Pointer-events-none, behind content.
// Visible-but-subtle in both themes; respects prefers-reduced-motion.
export function AnimatedBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      <div
        className="aria-blob absolute left-[5%] top-[-5%] h-[70vh] w-[70vh] rounded-full blur-[80px]"
        style={{
          background: "radial-gradient(circle, rgba(148,152,165,0.45), transparent 68%)",
        }}
      />
      <div
        className="aria-blob-2 absolute right-[2%] top-[15%] h-[62vh] w-[62vh] rounded-full blur-[90px]"
        style={{
          background: "radial-gradient(circle, rgba(190,194,206,0.40), transparent 68%)",
        }}
      />
      <div
        className="aria-blob-3 absolute bottom-[-12%] left-[28%] h-[68vh] w-[68vh] rounded-full blur-[90px]"
        style={{
          background: "radial-gradient(circle, rgba(120,124,138,0.38), transparent 68%)",
        }}
      />
      {/* Faint marigold accent — ties the ambient wash to the brand. */}
      <div
        className="aria-blob-2 absolute bottom-[8%] right-[14%] h-[42vh] w-[42vh] rounded-full blur-[100px]"
        style={{
          background: "radial-gradient(circle, rgba(240,162,60,0.16), transparent 70%)",
        }}
      />
    </div>
  );
}
