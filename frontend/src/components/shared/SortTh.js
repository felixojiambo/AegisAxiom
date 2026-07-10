import { useState } from 'react';
import { ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';

/**
 * Sortable table header cell.
 * Props match the pattern used across Commissions, Deals, SalesRepDashboard,
 * and AgentDashboard so all four can share a single implementation.
 */
export function SortTh({ col, label, sortCol, sortDir, onSort, className = '' }) {
  const active = sortCol === col;
  const Icon = active ? (sortDir === 'asc' ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <th
      onClick={() => onSort(col)}
      className={`text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider whitespace-nowrap cursor-pointer select-none group hover:text-aegisaxiom-text-secondary transition-colors ${className}`}
    >
      <div className="flex items-center gap-1">
        {label}
        <Icon
          size={11}
          className={`transition-colors ${active ? 'text-violet-600 dark:text-violet-400' : 'text-aegisaxiom-text-muted group-hover:text-aegisaxiom-text-muted'}`}
        />
      </div>
    </th>
  );
}

/**
 * Sorting state hook.
 * Returns { sortCol, sortDir, handleSort, sortFn } where sortFn(arr) returns
 * a sorted copy of the array based on the current sort state.
 */
export function useSorting(initialCol = '', initialDir = 'asc') {
  const [sortCol, setSortCol] = useState(initialCol);
  const [sortDir, setSortDir] = useState(initialDir);

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortCol(col);
      setSortDir('asc');
    }
  };

  const sortFn = (arr) => {
    if (!sortCol) return arr;
    return [...arr].sort((a, b) => {
      const av = a[sortCol] ?? '';
      const bv = b[sortCol] ?? '';
      const cmp =
        typeof av === 'number' && typeof bv === 'number'
          ? av - bv
          : String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: 'base' });
      return sortDir === 'asc' ? cmp : -cmp;
    });
  };

  return { sortCol, sortDir, handleSort, sortFn };
}
