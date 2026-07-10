/**
 * FinanceExportArtifact unit tests
 *
 * Covers the binary download path for the Nova chat artifact card.
 * The component previously used the JSON-only api.get helper and
 * called res.blob on it, which always threw and surfaced as a generic
 * network error even when the backend had returned a valid XLSX with
 * the PK signature. These tests lock the fix in place by asserting:
 *
 *   1. A 200 binary response triggers a real blob download.
 *   2. The success path never invokes JSON parsing on the response.
 *   3. Status codes map to the prescribed user facing wording.
 *   4. Content-Disposition filename beats the payload filename.
 *
 * Run with: cd frontend && yarn test --watchAll=false FinanceExportArtifact
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

jest.mock('../../../lib/apiConfig', () => ({
  __esModule: true,
  default: 'http://test-host',
}));

const mockApiDownloadRich = jest.fn();
jest.mock('../../../lib/api', () => ({
  __esModule: true,
  apiDownloadRich: (...args) => mockApiDownloadRich(...args),
}));

const { default: FinanceExportArtifact } = require('../FinanceExportArtifact');

function makeArtifact(overrides = {}) {
  return {
    artifact_type: 'finance_export',
    payload: {
      export_id: 'exp_test_1',
      filename: 'payables_2026_05_18.xlsx',
      format: 'xlsx',
      row_count: 42,
      source_tool_name: 'get_qbo_payables_metrics',
      generated_at: '2026-05-18T10:00:00+00:00',
      expires_at: '2026-05-25T10:00:00+00:00',
      download_url: '/api/nova/exports/exp_test_1/download',
      warnings: [],
      ...overrides,
    },
  };
}

const PK_SIGNATURE = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);

function makeXlsxBlob() {
  return new Blob([PK_SIGNATURE], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

function makeErrorBlob(detail) {
  return new Blob([JSON.stringify({ detail })], { type: 'application/json' });
}

beforeEach(() => {
  mockApiDownloadRich.mockReset();
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = jest.fn();
  }
  if (typeof URL.revokeObjectURL !== 'function') {
    URL.revokeObjectURL = jest.fn();
  }
  jest.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-url');
  jest.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe('FinanceExportArtifact metadata rendering', () => {
  it('shows filename, row count, source tool, created and expires', () => {
    render(<FinanceExportArtifact artifact={makeArtifact()} />);
    expect(screen.getByText('payables_2026_05_18.xlsx')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('get_qbo_payables_metrics')).toBeInTheDocument();
    expect(screen.getByText(/created/i)).toBeInTheDocument();
    expect(screen.getByText(/expires/i)).toBeInTheDocument();
    expect(screen.getByTestId('finance-export-download')).toBeEnabled();
  });

  it('renders a fallback when export_id is missing', () => {
    render(
      <FinanceExportArtifact artifact={makeArtifact({ export_id: undefined })} />,
    );
    expect(screen.getByText(/Export metadata is missing/i)).toBeInTheDocument();
  });
});

describe('FinanceExportArtifact 200 XLSX success path', () => {
  it('triggers a blob download with the payload filename', async () => {
    const xlsxBlob = makeXlsxBlob();
    mockApiDownloadRich.mockResolvedValue({
      ok: true,
      status: 200,
      blob: xlsxBlob,
      errorBlob: null,
      getHeader: () => null,
    });
    const clickSpy = jest
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});

    render(<FinanceExportArtifact artifact={makeArtifact()} />);
    fireEvent.click(screen.getByTestId('finance-export-download'));

    await waitFor(() => {
      expect(URL.createObjectURL).toHaveBeenCalledWith(xlsxBlob);
    });
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('finance-export-error')).toBeNull();
    expect(mockApiDownloadRich).toHaveBeenCalledWith(
      'http://test-host/api/nova/exports/exp_test_1/download',
    );
  });

  it('honours the server filename when Content-Disposition is set', async () => {
    mockApiDownloadRich.mockResolvedValue({
      ok: true,
      status: 200,
      blob: makeXlsxBlob(),
      errorBlob: null,
      getHeader: (name) =>
        name === 'Content-Disposition'
          ? 'attachment; filename="server_named_export.xlsx"'
          : null,
    });
    const downloadAttrs = [];
    const clickSpy = jest
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function trackDownload() {
        downloadAttrs.push(this.getAttribute('download'));
      });

    render(<FinanceExportArtifact artifact={makeArtifact()} />);
    fireEvent.click(screen.getByTestId('finance-export-download'));

    await waitFor(() => {
      expect(clickSpy).toHaveBeenCalled();
    });
    expect(downloadAttrs[0]).toBe('server_named_export.xlsx');
  });

  it('never parses the success body as JSON', async () => {
    // Spec acceptance check. A successful XLSX response is binary,
    // not JSON, and the success path must never feed it to a JSON
    // parser. We spy on the global parser and on Blob.text to prove
    // neither is touched on the 200 path.
    const xlsxBlob = makeXlsxBlob();
    const blobTextSpy = jest.spyOn(xlsxBlob, 'text');
    const jsonParseSpy = jest.spyOn(JSON, 'parse');

    mockApiDownloadRich.mockResolvedValue({
      ok: true,
      status: 200,
      blob: xlsxBlob,
      errorBlob: null,
      getHeader: () => null,
    });
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<FinanceExportArtifact artifact={makeArtifact()} />);
    fireEvent.click(screen.getByTestId('finance-export-download'));

    await waitFor(() => {
      expect(URL.createObjectURL).toHaveBeenCalledWith(xlsxBlob);
    });
    expect(blobTextSpy).not.toHaveBeenCalled();
    expect(jsonParseSpy).not.toHaveBeenCalled();
  });
});

describe('FinanceExportArtifact error envelopes', () => {
  async function runErrorCase(status, detail, expectedMessage) {
    mockApiDownloadRich.mockResolvedValue({
      ok: false,
      status,
      blob: null,
      errorBlob: detail !== null ? makeErrorBlob(detail) : null,
      getHeader: () => null,
    });
    jest.spyOn(console, 'error').mockImplementation(() => {});

    render(<FinanceExportArtifact artifact={makeArtifact()} />);
    fireEvent.click(screen.getByTestId('finance-export-download'));

    await waitFor(() => {
      expect(screen.getByTestId('finance-export-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('finance-export-error')).toHaveTextContent(
      expectedMessage,
    );
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  }

  it('401 shows the not authorised message', async () => {
    await runErrorCase(
      401,
      'Not authenticated',
      'You are not authorized to download this file.',
    );
  });

  it('403 shows the not authorised message', async () => {
    await runErrorCase(
      403,
      'Forbidden',
      'You are not authorized to download this file.',
    );
  });

  it('404 shows the no longer exists message', async () => {
    await runErrorCase(
      404,
      'Export not found',
      'This file no longer exists.',
    );
  });

  it('410 shows the expired message', async () => {
    await runErrorCase(
      410,
      'Export expired',
      'This export has expired. Generate it again.',
    );
  });

  it('status 0 shows the network error message', async () => {
    await runErrorCase(0, null, 'Network error during download.');
  });

  it('unknown status falls back to generic message without leaking detail', async () => {
    mockApiDownloadRich.mockResolvedValue({
      ok: false,
      status: 500,
      blob: null,
      errorBlob: makeErrorBlob('Traceback at line 412 in nova_engine.py'),
      getHeader: () => null,
    });
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});

    render(<FinanceExportArtifact artifact={makeArtifact()} />);
    fireEvent.click(screen.getByTestId('finance-export-download'));

    await waitFor(() => {
      expect(screen.getByTestId('finance-export-error')).toBeInTheDocument();
    });
    const userText = screen.getByTestId('finance-export-error').textContent || '';
    expect(userText).not.toMatch(/Traceback/);
    expect(userText).not.toMatch(/nova_engine/);
    expect(userText).toMatch(/Download failed/);
    expect(consoleError).toHaveBeenCalled();
  });

  it('does not parse XLSX bytes as JSON when status is 2xx but blob is missing', async () => {
    // Defensive case. If a future regression returns ok=true with a
    // null blob, the component must surface a clean error rather
    // than crash or attempt to JSON-parse a binary body.
    mockApiDownloadRich.mockResolvedValue({
      ok: true,
      status: 200,
      blob: null,
      errorBlob: null,
      getHeader: () => null,
    });
    jest.spyOn(console, 'error').mockImplementation(() => {});

    render(<FinanceExportArtifact artifact={makeArtifact()} />);
    fireEvent.click(screen.getByTestId('finance-export-download'));

    await waitFor(() => {
      expect(screen.getByTestId('finance-export-error')).toBeInTheDocument();
    });
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
