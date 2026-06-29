// Ambient backdrop, mounted once in AppShell (and on Login). A whisper-faint dot
// grid gives the surface some texture without fighting the text — no colored
// glow (the amber vignette/blobs were distracting and removed).
// pointer-events-none, below content.
export function AnimatedBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      {/* Faint dot-grid texture fills the surface (color is theme-aware). */}
      <div className="aria-dotgrid absolute inset-0" />
    </div>
  );
}
