// CSV download helper — ported from source lib/csv.js
const escapeCell = (v) => {
  if (v == null) return '';
  const s = String(v);
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
};

export function downloadCSV(filename, columns, rows) {
  const header = columns.map((c) => escapeCell(c.label)).join(',');
  const body = rows
    .map((r) => columns.map((c) => escapeCell(r[c.key])).join(','))
    .join('\r\n');
  const csv = `${header}\r\n${body}`;
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 500);
}
