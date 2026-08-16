/**
 * News. No provider is connected to the backend, so this page states that
 * plainly. Generating headlines would be fabricated content in a product whose
 * whole value proposition is that its numbers are real.
 */
import { EmptyState, SectionHeader } from "../components/ui/Primitives";

export default function NewsPage() {
  return (
    <div className="page">
      <SectionHeader title="News" sub="Market, stock and portfolio-relevant coverage" />
      <EmptyState
        title="No news provider connected"
        body="EquityLens has no news feed wired to the backend. Rather than show generated or placeholder headlines, this page stays empty until a real source is integrated."
        icon={
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M4 5h13v14H4zM17 9h3v8a2 2 0 0 1-2 2h-1zM7 9h7M7 13h7" />
          </svg>
        }
      />
    </div>
  );
}
