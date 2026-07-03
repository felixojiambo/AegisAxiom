import React, { useEffect, useState } from 'react';
import { ArrowLeft, Mail, Phone, Building2, X } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency, fmtDate } from '@/lib/financeFormat';

const BASE = `${API_URL}/api/finance`;

function Bucket({ label, v, bold, warn, bad }) {
  let cls = 'text-aegisaxiom-text';
  if (warn && v) cls = 'text-amber-400';
  if (bad && v) cls = 'text-red-400';
  return (
    <div className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated p-3">
      <div className="text-[11px] text-aegisaxiom-text-muted uppercase tracking-wider">{label}</div>
      <div
        className={`mt-1 font-mono tabular-nums text-[13px] ${cls} ${bold ? 'font-medium' : ''}`}
      >
        {fmtCurrency(v || 0)}
      </div>
    </div>
  );
}

export default function CustomerDetailModal({ customerId, onClose }) {
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    if (!customerId) return;
    setData(null);
    setLoadError(null);
    api
      .get(`${BASE}/lists/customers/${customerId}`)
      .then(async (r) => {
        if (!r.ok) {
          setLoadError(`Failed to load customer (${r.status})`);
          return;
        }
        setData(await r.json());
      })
      .catch((err) => {
        setLoadError(err?.message || 'Failed to load customer');
      });
  }, [customerId]);

  if (!customerId) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-2 md:p-4 overflow-y-auto"
      data-testid="customer-detail-modal"
    >
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-3xl mt-4 mb-4 md:mt-8 md:mb-8">
        <div className="flex items-center justify-between px-4 md:px-6 py-3 md:py-4 border-b border-aegisaxiom-border">
          <button
            onClick={onClose}
            className="inline-flex items-center gap-1.5 text-[13px] text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            data-testid="back-link"
          >
            <ArrowLeft className="h-3.5 w-3.5" />{' '}
            <span className="hidden sm:inline">Back to customers</span>
            <span className="sm:hidden">Back</span>
          </button>
          <button
            onClick={onClose}
            className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {loadError ? (
          <div className="p-12 text-center" data-testid="customer-detail-load-error">
            <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">
              Couldn't load customer
            </div>
            <div className="text-[13px] text-aegisaxiom-text-secondary mb-6">{loadError}</div>
          </div>
        ) : !data ? (
          <div
            className="py-20 text-center text-sm text-aegisaxiom-text-muted"
            data-testid="loading-customer-detail"
          >
            Loading…
          </div>
        ) : (
          (() => {
            const { info, aging, open_invoices, contact } = data;
            return (
              <div
                className="p-4 md:p-6 flex flex-col gap-5 md:gap-6"
                data-testid="page-customer-detail"
              >
                <div>
                  <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">
                    CUSTOMER
                  </p>
                  <h2 className="page-title">{info.name || 'Unnamed'}</h2>
                  <p className="text-sm text-aegisaxiom-text-secondary mt-1">
                    Customer ID {info.customer_id}
                  </p>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
                  {[
                    { label: 'Open Balance', value: fmtCurrency(info.balance) },
                    { label: 'YTD Income', value: fmtCurrency(info.income) },
                    { label: 'YTD Expenses', value: fmtCurrency(info.expenses) },
                    { label: 'YTD Net Income', value: fmtCurrency(info.net_income) },
                  ].map((s) => (
                    <div key={s.label} className="glass-card p-3 md:p-4">
                      <div className="text-[10px] md:text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
                        {s.label}
                      </div>
                      <div className="text-[16px] md:text-[22px] font-semibold tabular-nums text-aegisaxiom-text break-all">
                        {s.value}
                      </div>
                    </div>
                  ))}
                </div>

                {aging && (
                  <div className="glass-card p-4 md:p-6">
                    <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-3">
                      AR AGING
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
                      <Bucket label="Current" v={aging.current} />
                      <Bucket label="1–30" v={aging.d_1_30} />
                      <Bucket label="31–60" v={aging.d_31_60} warn />
                      <Bucket label="61–90" v={aging.d_61_90} warn />
                      <Bucket label="91+" v={aging.d_91_plus} bad />
                      <Bucket label="Total" v={aging.total} bold />
                    </div>
                  </div>
                )}

                {contact && (
                  <div className="glass-card p-4 md:p-6">
                    <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-3">
                      PRIMARY CONTACT
                    </div>
                    <div className="flex flex-col md:flex-row md:flex-wrap md:items-center gap-2 md:gap-6 text-[13px]">
                      <div className="flex items-center gap-2">
                        <Building2 className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
                        <span className="text-aegisaxiom-text">{contact.name}</span>
                      </div>
                      {contact.title && (
                        <div className="text-aegisaxiom-text-secondary">{contact.title}</div>
                      )}
                      {contact.email && (
                        <div className="flex items-center gap-2 text-aegisaxiom-text-secondary">
                          <Mail className="h-3.5 w-3.5" />
                          <span className="font-mono break-all">{contact.email}</span>
                        </div>
                      )}
                      {contact.phone && (
                        <div className="flex items-center gap-2 text-aegisaxiom-text-secondary">
                          <Phone className="h-3.5 w-3.5" />
                          <span className="font-mono">{contact.phone}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <div className="glass-card overflow-hidden">
                  <div className="px-4 md:px-5 py-3 border-b border-aegisaxiom-border">
                    <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">
                      OPEN INVOICES
                    </div>
                    <div className="text-[13px] text-aegisaxiom-text-secondary">
                      {(open_invoices || []).length} invoices outstanding
                    </div>
                  </div>
                  {!(open_invoices || []).length ? (
                    <div className="px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
                      No open invoices.
                    </div>
                  ) : (
                    <>
                      {/* Desktop table */}
                      <div className="hidden md:block overflow-x-auto">
                        <table className="w-full text-[13px]">
                          <thead>
                            <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                              {[
                                'Date',
                                'Type',
                                'Num',
                                'Due',
                                'Bucket',
                                'Amount',
                                'Open Balance',
                              ].map((h, i) => (
                                <th
                                  key={h}
                                  className={`px-5 py-2 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted ${i >= 5 ? 'text-right' : 'text-left'}`}
                                >
                                  {h}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {open_invoices.map((d, i) => (
                              <tr
                                key={i}
                                className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30"
                              >
                                <td className="px-5 py-2 font-mono text-aegisaxiom-text">
                                  {fmtDate(d.date)}
                                </td>
                                <td className="px-5 py-2 text-aegisaxiom-text-secondary">{d.txn_type}</td>
                                <td className="px-5 py-2 text-aegisaxiom-text">{d.num}</td>
                                <td className="px-5 py-2 font-mono text-aegisaxiom-text-secondary">
                                  {fmtDate(d.due_date)}
                                </td>
                                <td className="px-5 py-2 text-aegisaxiom-text-secondary">{d.bucket}</td>
                                <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text">
                                  {fmtCurrency(d.amount)}
                                </td>
                                <td className="px-5 py-2 text-right font-mono font-medium text-aegisaxiom-text">
                                  {fmtCurrency(d.open_balance)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      {/* Mobile card list */}
                      <div className="md:hidden p-3 flex flex-col gap-2">
                        {open_invoices.map((d, i) => (
                          <div
                            key={i}
                            className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3"
                            data-testid={`customer-invoice-card-${i}`}
                          >
                            <div className="flex items-start justify-between gap-2 mb-2">
                              <div className="min-w-0 flex-1">
                                <p className="text-[12px] font-medium text-aegisaxiom-text">#{d.num}</p>
                                <p className="text-[10px] text-aegisaxiom-text-muted mt-0.5">
                                  {d.txn_type}
                                </p>
                              </div>
                              <p className="text-[13px] font-mono font-medium text-aegisaxiom-text shrink-0">
                                {fmtCurrency(d.open_balance)}
                              </p>
                            </div>
                            <div className="flex items-center justify-between gap-2 pt-2 border-t border-aegisaxiom-border text-[11px]">
                              <span className="font-mono text-aegisaxiom-text-muted">
                                {fmtDate(d.date)} → {fmtDate(d.due_date)}
                              </span>
                              <span className="text-aegisaxiom-text-secondary truncate">
                                {d.bucket || '—'}
                              </span>
                            </div>
                            <div className="text-[11px] text-aegisaxiom-text-muted mt-1.5">
                              Amount:{' '}
                              <span className="font-mono text-aegisaxiom-text-secondary">
                                {fmtCurrency(d.amount)}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              </div>
            );
          })()
        )}
      </div>
    </div>
  );
}
