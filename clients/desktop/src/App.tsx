// Placeholder popover for the Phase 3 shell. The decision-first popover — hero,
// streaming reply, why, composer — lands in Phase 4 over the same window.
export function App() {
  return (
    <div className="popover">
      <header className="frosted-header">
        <span className="brand">✦ Wayfinder</span>
        <span className="status-chip">shell</span>
      </header>
      <main className="body">
        <p className="placeholder">
          Menu-bar shell is running. The decision-first popover lands in Phase 4.
        </p>
      </main>
    </div>
  );
}
