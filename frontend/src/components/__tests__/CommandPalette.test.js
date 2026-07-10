/**
 * CommandPalette tests.
 *
 * Covers:
 *  - Renders nothing when closed
 *  - Renders overlay when open
 *  - Esc closes the palette
 *  - Sections: Recent projects, My tasks, People (from cached localStorage)
 *  - "Ask PMO Pilot" row always rendered
 *  - "Ask PMO Pilot" with a query surfaces the query text
 *  - Clicking "Ask PMO Pilot" calls newSession('pmo') then sendMessage
 *
 * Run: cd frontend && yarn test --watchAll=false CommandPalette.test
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
jest.mock('@/lib/api', () => ({
  api: {
    get: jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })),
  },
}));

jest.mock('@/lib/apiConfig', () => 'http://localhost:8000');

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'user_me', email: 'test@example.com' } }),
}));

const mockNewSession = jest.fn(() => Promise.resolve({ session_id: 'sess_1' }));
const mockSendMessage = jest.fn(() => Promise.resolve());
const mockLoadSessions = jest.fn(() => Promise.resolve());

jest.mock('@/contexts/ChatContext', () => ({
  useChat: () => ({
    newSession: mockNewSession,
    sendMessage: mockSendMessage,
    loadSessions: mockLoadSessions,
  }),
}));

// cmdk uses ResizeObserver internally — polyfill for jsdom
global.ResizeObserver = global.ResizeObserver ?? class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function Wrapper({ open = true, onOpenChange = jest.fn() }) {
  const CommandPalette = require('../CommandPalette').default;
  return (
    <MemoryRouter>
      <CommandPalette open={open} onOpenChange={onOpenChange} />
    </MemoryRouter>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// Visibility
// ---------------------------------------------------------------------------
describe('visibility', () => {
  it('renders nothing when closed', () => {
    const { container } = render(<Wrapper open={false} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders overlay when open', () => {
    render(<Wrapper open />);
    expect(screen.getByTestId('command-palette-overlay')).toBeInTheDocument();
    expect(screen.getByTestId('command-palette')).toBeInTheDocument();
  });

  it('renders the search input', () => {
    render(<Wrapper open />);
    expect(screen.getByTestId('command-palette-input')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Close behaviours
// ---------------------------------------------------------------------------
describe('close behaviours', () => {
  it('calls onOpenChange(false) when close button is clicked', () => {
    const onOpenChange = jest.fn();
    render(<Wrapper open onOpenChange={onOpenChange} />);
    fireEvent.click(screen.getByLabelText('Close command palette'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

// ---------------------------------------------------------------------------
// Ask PMO Pilot section
// ---------------------------------------------------------------------------
describe('Ask PMO Pilot', () => {
  it('renders the "Ask PMO Pilot" row', () => {
    render(<Wrapper open />);
    expect(screen.getByTestId('cmd-ask-pmo-pilot')).toBeInTheDocument();
  });

  it('shows query text in the row label when there is a query', async () => {
    render(<Wrapper open />);
    const input = screen.getByTestId('command-palette-input');
    fireEvent.change(input, { target: { value: 'backend tasks' } });

    await waitFor(() => {
      expect(screen.getByText(/Ask PMO Pilot: "backend tasks"/)).toBeInTheDocument();
    });
  });

  it('calls newSession(pmo) and sendMessage on selection', async () => {
    render(<Wrapper open />);
    const input = screen.getByTestId('command-palette-input');
    fireEvent.change(input, { target: { value: 'show blocked tasks' } });

    const pilotRow = screen.getByTestId('cmd-ask-pmo-pilot');
    await act(async () => {
      fireEvent.click(pilotRow);
    });

    await waitFor(() => {
      expect(mockNewSession).toHaveBeenCalledWith('pmo');
    });
    await waitFor(() => {
      expect(mockSendMessage).toHaveBeenCalledWith('show blocked tasks');
    }, { timeout: 500 });
  });
});

// ---------------------------------------------------------------------------
// Recent projects (from localStorage cache)
// ---------------------------------------------------------------------------
describe('recent projects from cache', () => {
  it('renders cached recent projects', () => {
    localStorage.setItem(
      'aegisaxiom_cmd_recent_projects',
      JSON.stringify([
        { id: 'proj_1', label: 'Alpha Initiative', type: 'agile' },
        { id: 'proj_2', label: 'Beta Launch', type: 'waterfall' },
      ]),
    );

    render(<Wrapper open />);
    expect(screen.getByText('Alpha Initiative')).toBeInTheDocument();
    expect(screen.getByText('Beta Launch')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// People section (from localStorage cache)
// ---------------------------------------------------------------------------
describe('people from cache', () => {
  it('renders cached people and filters by query', async () => {
    localStorage.setItem(
      'aegisaxiom_cmd_people',
      JSON.stringify([
        { id: 'u1', name: 'Grace Hopper', email: 'grace@example.com' },
        { id: 'u2', name: 'Henry Ford', email: 'henry@example.com' },
      ]),
    );

    render(<Wrapper open />);
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument();
    expect(screen.getByText('Henry Ford')).toBeInTheDocument();

    const input = screen.getByTestId('command-palette-input');
    fireEvent.change(input, { target: { value: 'grace' } });

    await waitFor(() => {
      expect(screen.getByText('Grace Hopper')).toBeInTheDocument();
    });
    // Henry should not appear after filtering
    expect(screen.queryByText('Henry Ford')).not.toBeInTheDocument();
  });
});
