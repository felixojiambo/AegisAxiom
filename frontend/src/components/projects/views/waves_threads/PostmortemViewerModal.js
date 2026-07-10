import React, { useCallback } from 'react';
import { format, parseISO } from 'date-fns';
import { X, FileText, Download } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { jsPDF } from 'jspdf';
import { usePermissions } from '@/hooks/usePermissions';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function safeFormatDate(iso) {
  if (!iso) return '—';
  try {
    return format(parseISO(iso), 'MMM d, yyyy');
  } catch {
    return iso;
  }
}

function sanitizeFilename(s) {
  return (s ?? 'project')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40);
}

const BUCKET_PRIORITY_STYLES = {
  critical: 'bg-red-500/15 text-red-400',
  high:     'bg-orange-500/15 text-orange-400',
  medium:   'bg-yellow-500/15 text-yellow-400',
  low:      'bg-aegisaxiom-elevated text-aegisaxiom-text-muted',
};

function PriorityBadge({ priority }) {
  const cls = BUCKET_PRIORITY_STYLES[priority] ?? BUCKET_PRIORITY_STYLES.low;
  return (
    <span className={`inline-flex items-center h-5 px-1.5 rounded text-[10px] font-semibold uppercase ${cls}`}>
      {priority ?? 'low'}
    </span>
  );
}

function ModuleChip({ module }) {
  if (!module) return null;
  return (
    <span className="inline-flex items-center h-5 px-2 rounded bg-violet-500/15 text-violet-400 text-[10px] font-medium">
      {module}
    </span>
  );
}

function DeveloperChip({ name }) {
  return (
    <span className="inline-flex items-center h-6 px-2 rounded-md bg-aegisaxiom-elevated text-aegisaxiom-text text-xs font-medium">
      {name}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Section sub-components
// ---------------------------------------------------------------------------

function SectionHeading({ children }) {
  return (
    <h4 className="text-[11px] uppercase font-bold tracking-wider text-aegisaxiom-text-muted mb-2">
      {children}
    </h4>
  );
}

function HeaderSection({ postmortem }) {
  const { wave_number, start_date, end_date, developers } = postmortem;
  const devList = Array.isArray(developers) ? developers : [];

  return (
    <section>
      <SectionHeading>Header</SectionHeading>
      <div className="bg-aegisaxiom-elevated rounded-lg p-3 space-y-2">
        <div className="flex flex-wrap items-center gap-4 text-xs text-aegisaxiom-text">
          {wave_number != null && (
            <span>Wave <span className="font-semibold">{wave_number}</span></span>
          )}
          {start_date && (
            <span>Started <span className="font-semibold">{safeFormatDate(start_date)}</span></span>
          )}
          {end_date && (
            <span>Ended <span className="font-semibold">{safeFormatDate(end_date)}</span></span>
          )}
        </div>
        {devList.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {devList.map((d, i) => (
              <DeveloperChip key={i} name={typeof d === 'string' ? d : (d.name ?? JSON.stringify(d))} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function ThreadStatusSection({ thread_status }) {
  if (!Array.isArray(thread_status) || thread_status.length === 0) {
    return (
      <section>
        <SectionHeading>Thread Status</SectionHeading>
        <p className="text-xs text-aegisaxiom-text-muted">No thread status data.</p>
      </section>
    );
  }

  return (
    <section>
      <SectionHeading>Thread Status</SectionHeading>
      <div className="overflow-x-auto rounded-lg border border-aegisaxiom-border">
        <table className="w-full text-xs">
          <thead>
            <tr className="h-8 bg-aegisaxiom-elevated border-b border-aegisaxiom-border text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted">
              <th className="text-left px-3 font-medium">Thread</th>
              <th className="text-left px-3 font-medium">Owner</th>
              <th className="text-left px-3 font-medium">Scoped</th>
              <th className="text-left px-3 font-medium">Shipped</th>
              <th className="text-left px-3 font-medium">Drift</th>
              <th className="text-left px-3 font-medium">Blockers</th>
            </tr>
          </thead>
          <tbody>
            {thread_status.map((t, i) => (
              <tr key={i} className="border-t border-aegisaxiom-border h-9 text-aegisaxiom-text">
                <td className="px-3 font-medium">{t.thread_id ?? t.id ?? `Thread ${i + 1}`}</td>
                <td className="px-3 text-aegisaxiom-text-muted">{t.owner ?? '—'}</td>
                <td className="px-3">{t.scoped_count ?? t.scoped ?? '—'}</td>
                <td className="px-3">{t.shipped_summary ?? t.shipped ?? '—'}</td>
                <td className="px-3">{t.scope_drift ?? '—'}</td>
                <td className="px-3">{t.blockers_summary ?? t.blockers ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function BucketColumn({ label, items, colorClass }) {
  return (
    <div className="flex-1 min-w-0">
      <p className={`text-[11px] font-bold uppercase tracking-wide mb-2 ${colorClass}`}>{label}</p>
      {items.length === 0 ? (
        <p className="text-xs text-aegisaxiom-text-muted">None</p>
      ) : (
        <div className="space-y-2">
          {items.map((item, i) => (
            <div key={i} className="bg-aegisaxiom-elevated rounded-md p-2 space-y-1">
              <p className="text-xs text-aegisaxiom-text font-medium leading-snug">
                {item.title ?? item.name ?? JSON.stringify(item)}
              </p>
              <div className="flex items-center gap-1.5 flex-wrap">
                {item.module_owned && <ModuleChip module={item.module_owned} />}
                {item.priority && <PriorityBadge priority={item.priority} />}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BucketsSection({ buckets }) {
  if (!buckets || typeof buckets !== 'object') {
    return (
      <section>
        <SectionHeading>Buckets</SectionHeading>
        <p className="text-xs text-aegisaxiom-text-muted">No bucket data.</p>
      </section>
    );
  }

  const mustFix  = Array.isArray(buckets.must_fix)    ? buckets.must_fix    : [];
  const shouldAdd = Array.isArray(buckets.should_add) ? buckets.should_add  : [];
  const watch     = Array.isArray(buckets.watch)       ? buckets.watch       : [];

  return (
    <section>
      <SectionHeading>Buckets</SectionHeading>
      <div className="flex gap-4">
        <BucketColumn label="Must Fix"   items={mustFix}   colorClass="text-red-400"    />
        <BucketColumn label="Should Add" items={shouldAdd} colorClass="text-yellow-400" />
        <BucketColumn label="Watch"      items={watch}     colorClass="text-blue-400"   />
      </div>
    </section>
  );
}

function MarkdownSection({ heading, content }) {
  if (!content) {
    return (
      <section>
        <SectionHeading>{heading}</SectionHeading>
        <p className="text-xs text-aegisaxiom-text-muted">—</p>
      </section>
    );
  }

  const text = typeof content === 'string' ? content : JSON.stringify(content, null, 2);

  return (
    <section>
      <SectionHeading>{heading}</SectionHeading>
      <div className="prose prose-sm max-w-none text-aegisaxiom-text text-xs bg-aegisaxiom-elevated rounded-lg p-3
                      prose-headings:text-aegisaxiom-text prose-p:text-aegisaxiom-text prose-li:text-aegisaxiom-text
                      prose-strong:text-aegisaxiom-text prose-code:text-violet-400
                      prose-ul:my-1 prose-li:my-0 prose-p:my-1">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// PDF export
// ---------------------------------------------------------------------------

function buildPDF(wave, projectName) {
  const doc = new jsPDF({ orientation: 'portrait', unit: 'pt', format: 'a4' });
  const PAGE_W = doc.internal.pageSize.getWidth();
  const MARGIN = 40;
  const COL_W  = PAGE_W - MARGIN * 2;
  let y = MARGIN;

  const { postmortem } = wave;

  // ---- helpers ----
  function ensurePage(needed = 40) {
    if (y + needed > doc.internal.pageSize.getHeight() - MARGIN) {
      doc.addPage();
      y = MARGIN;
    }
  }

  function heading1(text) {
    ensurePage(30);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(16);
    doc.setTextColor(30, 30, 30);
    doc.text(text, MARGIN, y);
    y += 22;
  }

  function heading2(text) {
    ensurePage(24);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(11);
    doc.setTextColor(80, 80, 80);
    doc.text(text.toUpperCase(), MARGIN, y);
    y += 4;
    doc.setDrawColor(200, 200, 200);
    doc.line(MARGIN, y, MARGIN + COL_W, y);
    y += 14;
  }

  function bodyText(text, indent = 0) {
    ensurePage(16);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(50, 50, 50);
    const lines = doc.splitTextToSize(text, COL_W - indent);
    doc.text(lines, MARGIN + indent, y);
    y += lines.length * 12;
  }

  function chip(text, x, chipY, r, g, b) {
    doc.setFillColor(r, g, b);
    doc.roundedRect(x, chipY - 8, doc.getTextWidth(text) + 10, 12, 3, 3, 'F');
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8);
    doc.setTextColor(255, 255, 255);
    doc.text(text, x + 5, chipY);
    return doc.getTextWidth(text) + 14;
  }

  function drawTable(headers, rows) {
    const colCount = headers.length;
    const colW = Math.floor(COL_W / colCount);
    const rowH = 18;

    ensurePage(rowH + 8);

    // Header row
    doc.setFillColor(240, 240, 245);
    doc.rect(MARGIN, y, COL_W, rowH, 'F');
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(8);
    doc.setTextColor(80, 80, 80);
    headers.forEach((h, i) => doc.text(h, MARGIN + i * colW + 4, y + 12));
    y += rowH;

    // Data rows
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8);
    doc.setTextColor(50, 50, 50);
    rows.forEach((row, ri) => {
      ensurePage(rowH);
      if (ri % 2 === 1) {
        doc.setFillColor(250, 250, 252);
        doc.rect(MARGIN, y, COL_W, rowH, 'F');
      }
      row.forEach((cell, ci) => {
        const cellStr = String(cell ?? '—');
        const clipped = doc.splitTextToSize(cellStr, colW - 8)[0];
        doc.text(clipped, MARGIN + ci * colW + 4, y + 12);
      });
      y += rowH;
    });
    y += 8;
  }

  // ---- Title ----
  heading1(`Wave ${wave.wave_number ?? '?'} — Postmortem`);
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(9);
  doc.setTextColor(120, 120, 120);
  doc.text(`Project: ${projectName}   ·   Generated: ${format(new Date(), 'MMM d, yyyy')}`, MARGIN, y);
  y += 20;

  if (!postmortem) {
    bodyText('No postmortem data recorded.');
    return doc;
  }

  // ---- Header section ----
  heading2('Header');
  const { wave_number, start_date, end_date, developers } = postmortem;
  const headerLine = [
    wave_number != null ? `Wave ${wave_number}` : null,
    start_date ? `Start: ${safeFormatDate(start_date)}` : null,
    end_date   ? `End: ${safeFormatDate(end_date)}`     : null,
  ].filter(Boolean).join('   ·   ');
  if (headerLine) bodyText(headerLine);

  const devList = Array.isArray(developers) ? developers : [];
  if (devList.length > 0) {
    ensurePage(24);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    doc.setTextColor(80, 80, 80);
    doc.text('Developers:', MARGIN, y);
    y += 14;
    let dx = MARGIN;
    devList.forEach((d) => {
      const name = typeof d === 'string' ? d : (d.name ?? '?');
      ensurePage(20);
      if (dx + 80 > MARGIN + COL_W) { dx = MARGIN; y += 16; }
      dx += chip(name, dx, y, 100, 80, 180) + 4;
    });
    y += 16;
  }
  y += 8;

  // ---- Thread status table ----
  heading2('Thread Status');
  const threads = Array.isArray(postmortem.thread_status) ? postmortem.thread_status : [];
  if (threads.length > 0) {
    drawTable(
      ['Thread', 'Owner', 'Scoped', 'Shipped', 'Drift', 'Blockers'],
      threads.map((t) => [
        t.thread_id ?? t.id ?? '?',
        t.owner ?? '—',
        t.scoped_count ?? t.scoped ?? '—',
        t.shipped_summary ?? t.shipped ?? '—',
        t.scope_drift ?? '—',
        t.blockers_summary ?? t.blockers ?? '—',
      ]),
    );
  } else {
    bodyText('No thread status data.');
    y += 8;
  }

  // ---- Buckets ----
  heading2('Buckets');
  const buckets = postmortem.buckets ?? {};
  ['must_fix', 'should_add', 'watch'].forEach((key) => {
    const label = { must_fix: 'MUST FIX', should_add: 'SHOULD ADD', watch: 'WATCH' }[key];
    const items = Array.isArray(buckets[key]) ? buckets[key] : [];
    ensurePage(20);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    doc.setTextColor(80, 80, 80);
    doc.text(`${label} (${items.length})`, MARGIN, y);
    y += 12;
    if (items.length === 0) {
      bodyText('None', 12);
    } else {
      items.forEach((item) => {
        const title = item.title ?? item.name ?? JSON.stringify(item);
        const meta  = [item.module_owned, item.priority].filter(Boolean).join(' · ');
        bodyText(`• ${title}`, 12);
        if (meta) bodyText(`  ${meta}`, 12);
      });
    }
    y += 6;
  });
  y += 6;

  // ---- Discoveries ----
  heading2('Discoveries');
  const discoveries = postmortem.discoveries;
  if (discoveries) {
    const text = typeof discoveries === 'string' ? discoveries : JSON.stringify(discoveries, null, 2);
    const lines = doc.splitTextToSize(text, COL_W);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(50, 50, 50);
    lines.forEach((line) => {
      ensurePage(14);
      doc.text(line, MARGIN, y);
      y += 12;
    });
  } else {
    bodyText('—');
  }
  y += 10;

  // ---- Stakeholder update ----
  heading2('Stakeholder Update');
  const stakeholder = postmortem.stakeholder_update;
  if (stakeholder) {
    const text = typeof stakeholder === 'string' ? stakeholder : JSON.stringify(stakeholder, null, 2);
    const lines = doc.splitTextToSize(text, COL_W);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(50, 50, 50);
    lines.forEach((line) => {
      ensurePage(14);
      doc.text(line, MARGIN, y);
      y += 12;
    });
  } else {
    bodyText('—');
  }

  return doc;
}

// ---------------------------------------------------------------------------
// Main modal
// ---------------------------------------------------------------------------

export default function PostmortemViewerModal({ wave, projectName, onClose }) {
  const { hasPermission } = usePermissions();
  const canExport = hasPermission('projects.wave.view');

  const handleExport = useCallback(() => {
    if (!wave) return;
    const doc = buildPDF(wave, projectName ?? 'project');
    const slug = sanitizeFilename(projectName);
    const date = format(new Date(), 'yyyy-MM-dd');
    const waveNum = wave.wave_number ?? 'x';
    doc.save(`Postmortem_Wave-${waveNum}_${slug}_${date}.pdf`);
  }, [wave, projectName]);

  if (!wave) return null;

  const { postmortem } = wave;

  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="postmortem-viewer-backdrop"
    >
      <div
        className="glass-card w-full max-w-[720px] max-h-[88vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Postmortem viewer"
        data-testid="postmortem-viewer"
      >
        {/* Header bar */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-aegisaxiom-border shrink-0">
          <div className="flex items-center gap-2">
            <FileText size={14} className="text-aegisaxiom-text-muted" />
            <h3 className="text-sm font-bold text-aegisaxiom-text">
              Wave {wave.wave_number} — Postmortem
            </h3>
          </div>
          <div className="flex items-center gap-2">
            {canExport && postmortem && (
              <button
                type="button"
                onClick={handleExport}
                className="inline-flex items-center gap-1.5 h-7 px-3 rounded-md border border-aegisaxiom-border text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
                data-testid="postmortem-export-pdf"
              >
                <Download size={12} />
                Export PDF
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="inline-flex items-center justify-center h-7 w-7 rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
              aria-label="Close"
              data-testid="postmortem-close"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto flex-1 px-5 py-4 space-y-5">
          {postmortem ? (
            <>
              <HeaderSection postmortem={postmortem} />
              <ThreadStatusSection thread_status={postmortem.thread_status} />
              <BucketsSection buckets={postmortem.buckets} />
              <MarkdownSection heading="Discoveries" content={postmortem.discoveries} />
              <MarkdownSection heading="Stakeholder Update" content={postmortem.stakeholder_update} />
            </>
          ) : (
            <p className="text-sm text-aegisaxiom-text-muted">No postmortem recorded yet.</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end px-5 py-3 border-t border-aegisaxiom-border shrink-0">
          <button type="button" onClick={onClose} className="btn-secondary h-8 px-3 text-xs">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
