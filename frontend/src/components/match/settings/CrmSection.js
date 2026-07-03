import React, { useState } from 'react';
import { CheckCircle2, XCircle, Loader2, Database, Upload, Globe } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import SecretField from './SecretField';

/**
 * CrmSection
 *
 * Top-level connection_mode selector: snapshot | live_sql | live_api
 *
 * snapshot  — no connection needed; note shown
 * live_sql  — SQL server config with SecretField for service_password
 *             + Test SQL Connection button
 * live_api  — base_url, api_key (SecretField), customers_endpoint
 *             (no test endpoint in v1 — documented below)
 */

const MODES = [
  { value: 'snapshot', label: 'Manual CSV Upload', Icon: Upload },
  { value: 'live_sql', label: 'Live SQL', Icon: Database },
  { value: 'live_api', label: 'Live API', Icon: Globe },
];

const DEFAULT_READ_TABLES = ['customers', 'orders', 'order_attributes'];

export default function CrmSection({ value = {}, onChange, readOnly }) {
  const mode = value.connection_mode ?? 'snapshot';
  const sql = value.sql ?? {};
  const apiConn = value.api ?? {};

  const [testState, setTestState] = useState('idle');
  const [testMessage, setTestMessage] = useState('');
  const [testLatency, setTestLatency] = useState(null);
  const [tablesVerified, setTablesVerified] = useState([]);
  const [tableChipInput, setTableChipInput] = useState('');

  function emitChange(changes) {
    onChange({ ...value, ...changes });
  }

  function handleModeChange(newMode) {
    setTestState('idle');
    emitChange({ connection_mode: newMode });
  }

  function patchSql(field, val) {
    emitChange({ sql: { ...sql, [field]: val } });
  }

  function patchApi(field, val) {
    emitChange({ api: { ...apiConn, [field]: val } });
  }

  // Chip-list handlers for read_tables
  const readTables = sql.read_tables ?? [...DEFAULT_READ_TABLES];

  function addTable(name) {
    const trimmed = name.trim().toLowerCase();
    if (!trimmed || readTables.includes(trimmed)) return;
    patchSql('read_tables', [...readTables, trimmed]);
    setTableChipInput('');
  }

  function removeTable(name) {
    patchSql('read_tables', readTables.filter((t) => t !== name));
  }

  async function handleTestSql() {
    setTestState('testing');
    setTestMessage('');
    setTestLatency(null);
    setTablesVerified([]);
    try {
      const res = await api.post(`${API_URL}/api/match/settings/test-sql`);
      const data = await res.json();
      if (res.ok && data.status === 'success') {
        setTestState('success');
        setTestMessage(data.message ?? 'Connected');
        setTestLatency(data.latency_ms ?? null);
        setTablesVerified(data.tables_verified ?? []);
        setTimeout(() => setTestState('idle'), 12000);
      } else {
        setTestState('error');
        setTestMessage(data.message ?? data.detail ?? 'SQL test failed');
      }
    } catch {
      setTestState('error');
      setTestMessage('Could not reach backend');
    }
  }

  return (
    <div className="space-y-5">
      {/* Mode selector */}
      <fieldset>
        <legend className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-2.5">
          Connection Mode
        </legend>
        <div className="flex flex-wrap gap-2.5">
          {MODES.map(({ value: modeVal, label, Icon }) => (
            <button
              key={modeVal}
              type="button"
              role="radio"
              aria-checked={mode === modeVal}
              disabled={readOnly}
              onClick={() => handleModeChange(modeVal)}
              className={`flex items-center gap-2 rounded-lg border px-3.5 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 ${
                mode === modeVal
                  ? 'border-aegisaxiom-primary bg-aegisaxiom-primary/10 text-aegisaxiom-text'
                  : 'border-aegisaxiom-border bg-aegisaxiom-bg-elevated text-aegisaxiom-text-muted hover:border-aegisaxiom-primary/40 hover:text-aegisaxiom-text'
              } ${readOnly ? 'opacity-60 cursor-default' : 'cursor-pointer'}`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>
      </fieldset>

      {/* ── Snapshot note ── */}
      {mode === 'snapshot' && (
        <div className="glass-card rounded-lg border border-aegisaxiom-border p-4 text-sm text-aegisaxiom-text-muted">
          Snapshot mode reads from uploaded CSV files at session-create time; no live connection needed.
          Upload customers and orders snapshots via the New Session screen each run.
        </div>
      )}

      {/* ── Live SQL ── */}
      {mode === 'live_sql' && (
        <div className="glass-card rounded-lg border border-aegisaxiom-border p-4 space-y-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
            SQL Server Configuration
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <LabeledInput
              id="sql-server"
              label="Server"
              placeholder="appdirect-sql.internal"
              value={sql.server ?? ''}
              onChange={(v) => patchSql('server', v)}
              disabled={readOnly}
            />
            <LabeledInput
              id="sql-database"
              label="Database"
              placeholder="provider_sales_prod"
              value={sql.database ?? ''}
              onChange={(v) => patchSql('database', v)}
              disabled={readOnly}
            />
            <div className="space-y-1">
              <label htmlFor="sql-port" className="block text-xs font-medium text-aegisaxiom-text-muted">
                Port
              </label>
              <input
                id="sql-port"
                type="number"
                min="1"
                max="65535"
                className="input-field w-full text-sm"
                value={sql.port ?? 1433}
                onChange={(e) => patchSql('port', parseInt(e.target.value, 10) || 1433)}
                disabled={readOnly}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <label htmlFor="sql-auth-mode" className="block text-xs font-medium text-aegisaxiom-text-muted">
                Auth Mode
              </label>
              <select
                id="sql-auth-mode"
                className="input-field w-full text-sm"
                value={sql.auth_mode ?? 'service_account'}
                onChange={(e) => patchSql('auth_mode', e.target.value)}
                disabled={readOnly}
              >
                <option value="service_account">Service Account</option>
                <option value="integrated">Integrated</option>
              </select>
            </div>
            <LabeledInput
              id="sql-user"
              label="Service User"
              placeholder="svc_match_readonly"
              value={sql.service_user ?? ''}
              onChange={(v) => patchSql('service_user', v)}
              disabled={readOnly}
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="sql-pass" className="block text-xs font-medium text-aegisaxiom-text-muted">
              Service Password
              <span className="ml-1 font-normal text-aegisaxiom-text-muted">(encrypted)</span>
            </label>
            <SecretField
              id="sql-pass"
              value={sql.service_password ?? ''}
              onChange={(v) => patchSql('service_password', v)}
              placeholder="Service account password"
              disabled={readOnly}
            />
          </div>

          {/* Read tables chip list */}
          <div className="space-y-2">
            <div className="text-xs font-medium text-aegisaxiom-text-muted">Read Tables</div>
            <div className="flex flex-wrap gap-1.5">
              {readTables.map((t) => (
                <span
                  key={t}
                  className="inline-flex items-center gap-1 rounded-full border border-aegisaxiom-border bg-aegisaxiom-bg-elevated px-2.5 py-0.5 text-xs text-aegisaxiom-text"
                >
                  {t}
                  {!readOnly && (
                    <button
                      type="button"
                      className="text-aegisaxiom-text-muted hover:text-red-400 transition-colors ml-0.5"
                      onClick={() => removeTable(t)}
                      aria-label={`Remove ${t}`}
                    >
                      &times;
                    </button>
                  )}
                </span>
              ))}
            </div>
            {!readOnly && (
              <div className="flex gap-2">
                <input
                  type="text"
                  className="input-field text-sm w-40"
                  placeholder="table_name"
                  value={tableChipInput}
                  onChange={(e) => setTableChipInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ',') {
                      e.preventDefault();
                      addTable(tableChipInput);
                    }
                  }}
                />
                <button
                  type="button"
                  className="btn-secondary text-xs px-3"
                  onClick={() => addTable(tableChipInput)}
                >
                  Add
                </button>
              </div>
            )}
          </div>

          {/* Test SQL */}
          <div className="border-t border-aegisaxiom-border pt-4 flex items-center justify-between gap-4">
            <div aria-live="polite" className="flex flex-col gap-0.5 text-xs">
              {testState === 'idle' && (
                <span className="text-aegisaxiom-text-muted">Save changes first, then test.</span>
              )}
              {testState === 'testing' && (
                <span className="flex items-center gap-1.5 text-aegisaxiom-text-muted">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Testing SQL...
                </span>
              )}
              {testState === 'success' && (
                <>
                  <span className="flex items-center gap-1.5" style={{ color: 'var(--color-success, #22c55e)' }}>
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    {testMessage}{testLatency != null ? ` (${testLatency}ms)` : ''}
                  </span>
                  {tablesVerified.length > 0 && (
                    <span className="text-aegisaxiom-text-muted pl-5">
                      Verified: {tablesVerified.join(', ')}
                    </span>
                  )}
                </>
              )}
              {testState === 'error' && (
                <span className="flex items-center gap-1.5 text-red-400">
                  <XCircle className="h-3.5 w-3.5" /> {testMessage}
                </span>
              )}
            </div>
            <button
              type="button"
              className="btn-secondary text-sm flex items-center gap-1.5"
              disabled={testState === 'testing' || readOnly}
              onClick={handleTestSql}
            >
              Test SQL Connection
            </button>
          </div>

          <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-bg-elevated px-3 py-2 text-xs text-aegisaxiom-text-muted">
            Note: Live SQL read is a v1 stub. Connection details are saved so the switch is ready
            when AppDirect grants access. All matching currently uses the Manual CSV path.
          </div>
        </div>
      )}

      {/* ── Live API ── */}
      {mode === 'live_api' && (
        <div className="glass-card rounded-lg border border-aegisaxiom-border p-4 space-y-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
            API Connection
          </div>

          <LabeledInput
            id="crm-api-url"
            label="Base URL"
            placeholder="https://api.your-crm.com"
            value={apiConn.base_url ?? ''}
            onChange={(v) => patchApi('base_url', v)}
            disabled={readOnly}
          />

          <div className="space-y-1">
            <label htmlFor="crm-api-key" className="block text-xs font-medium text-aegisaxiom-text-muted">
              API Key
              <span className="ml-1 font-normal text-aegisaxiom-text-muted">(encrypted)</span>
            </label>
            <SecretField
              id="crm-api-key"
              value={apiConn.api_key ?? ''}
              onChange={(v) => patchApi('api_key', v)}
              placeholder="Paste API key"
              disabled={readOnly}
            />
          </div>

          <LabeledInput
            id="crm-customers-endpoint"
            label="Customers Endpoint"
            placeholder="/v1/customers"
            value={apiConn.customers_endpoint ?? ''}
            onChange={(v) => patchApi('customers_endpoint', v)}
            disabled={readOnly}
          />

          <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-bg-elevated px-3 py-2 text-xs text-aegisaxiom-text-muted">
            Note: A "Test API Connection" button is not available in v1 — the backend test endpoint
            for live_api mode is not yet implemented.
          </div>
        </div>
      )}
    </div>
  );
}

function LabeledInput({ id, label, placeholder, value, onChange, disabled }) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-aegisaxiom-text-muted">
        {label}
      </label>
      <input
        id={id}
        type="text"
        className="input-field w-full text-sm"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
    </div>
  );
}
