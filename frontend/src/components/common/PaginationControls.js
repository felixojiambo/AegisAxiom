/**
 * PaginationControls — server-side pagination footer.
 *
 * The Contracts list and Projects list both consume backend responses
 * shaped {items, total, skip, limit}. Both pages need identical UX:
 * a "Showing X–Y of Z" label, prev/next buttons, and a page-size
 * selector. Co-locating that here keeps both pages aligned and lets
 * future list pages reuse the same control.
 *
 * Server-side, not client-slice. Callers own:
 *   - state: page (number) + pageSize (number)
 *   - effect: when filters change, reset page to 1
 *   - fetch: GET …?skip=page*pageSize&limit=pageSize
 *
 * This component is purely presentational. It does NOT mutate the URL,
 * it does NOT hold state, and it does NOT debounce — callers do that
 * upstream so each page can pick its own filter-change semantics.
 */
import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

const DEFAULT_PAGE_SIZE_OPTIONS = [25, 50, 100];

export default function PaginationControls({
  page,
  pageSize,
  total,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  onPageChange,
  onPageSizeChange,
  loading = false,
  testid = 'pagination-controls',
}) {
  const safeTotal = Math.max(0, Number.isFinite(total) ? Math.floor(total) : 0);
  const safePageSize = Math.max(1, Number.isFinite(pageSize) ? Math.floor(pageSize) : 25);
  const totalPages = Math.max(1, Math.ceil(safeTotal / safePageSize));
  // Clamp page to [1, totalPages] so a stale page index doesn't show
  // a nonsense range like "76-100 of 12".
  const safePage = Math.min(Math.max(1, Math.floor(page) || 1), totalPages);

  const startIdx = safeTotal === 0 ? 0 : (safePage - 1) * safePageSize + 1;
  const endIdx = Math.min(safeTotal, safePage * safePageSize);

  const canPrev = safePage > 1 && !loading;
  const canNext = safePage < totalPages && !loading;

  return (
    <div
      data-testid={testid}
      className="flex items-center justify-between gap-3 flex-wrap py-3"
    >
      <div className="flex items-center gap-3 text-xs text-aegisaxiom-text-secondary">
        <span data-testid={`${testid}-range`}>
          {safeTotal === 0
            ? 'No results'
            : `Showing ${startIdx}–${endIdx} of ${safeTotal}`}
        </span>
        <label className="flex items-center gap-1.5">
          <span>Rows:</span>
          <select
            value={safePageSize}
            onChange={(e) => onPageSizeChange?.(parseInt(e.target.value, 10))}
            className="aegisaxiom-input text-xs h-7 px-2 py-0"
            data-testid={`${testid}-page-size`}
            disabled={loading}
          >
            {pageSizeOptions.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => canPrev && onPageChange?.(safePage - 1)}
          disabled={!canPrev}
          className="inline-flex items-center gap-1 px-2 py-1 rounded border border-aegisaxiom-border text-xs text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text disabled:opacity-40 disabled:cursor-not-allowed"
          data-testid={`${testid}-prev`}
          aria-label="Previous page"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
          Prev
        </button>
        <span
          className="px-2 text-xs text-aegisaxiom-text-muted"
          data-testid={`${testid}-page-label`}
        >
          Page {safePage} of {totalPages}
        </span>
        <button
          type="button"
          onClick={() => canNext && onPageChange?.(safePage + 1)}
          disabled={!canNext}
          className="inline-flex items-center gap-1 px-2 py-1 rounded border border-aegisaxiom-border text-xs text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text disabled:opacity-40 disabled:cursor-not-allowed"
          data-testid={`${testid}-next`}
          aria-label="Next page"
        >
          Next
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
