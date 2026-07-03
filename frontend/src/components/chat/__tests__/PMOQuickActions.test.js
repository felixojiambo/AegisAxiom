/**
 * PMOQuickActions — unit tests (React Testing Library)
 *
 * Key assertions:
 *  1. Component renders only when effectivePinnedAgent === 'pmo'
 *  2. Chips are hidden after the first user message (messageCount > 0)
 *  3. Clicking a chip fires onSend with the correct message text
 *  4. "Client update for..." opens the inline project picker
 *  5. Onboarding banner renders on first visit; dismisses on X
 *
 * Run with: cd frontend && yarn test --watchAll=false PMOQuickActions
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// localStorage mock (jsdom provides one; we just spy on it)
beforeEach(() => {
  localStorage.clear();
});

const mockUser = { user_id: 'u_pm', role: 'project_manager', name: 'Test PM' };

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: mockUser }),
}));

// ---------------------------------------------------------------------------
// Component under test
// ---------------------------------------------------------------------------

const { default: PMOQuickActions } = require('../PMOQuickActions');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderChips(props = {}) {
  const defaults = {
    effectivePinnedAgent: 'pmo',
    messageCount: 0,
    onSend: jest.fn(),
  };
  return render(<PMOQuickActions {...defaults} {...props} />);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('PMOQuickActions — agent gating', () => {
  it('renders when agent is pmo', () => {
    renderChips({ effectivePinnedAgent: 'pmo' });
    expect(screen.getByTestId('pmo-quick-actions')).toBeInTheDocument();
  });

  it('does NOT render for non-pmo agents', () => {
    const { container } = renderChips({ effectivePinnedAgent: 'nova' });
    expect(container.firstChild).toBeNull();
  });

  it('does NOT render when no agent is pinned', () => {
    const { container } = renderChips({ effectivePinnedAgent: null });
    expect(container.firstChild).toBeNull();
  });
});

describe('PMOQuickActions — message-count gating', () => {
  it('hides after the first user message', () => {
    const { container } = renderChips({ messageCount: 1 });
    expect(container.firstChild).toBeNull();
  });

  it('still visible with messageCount === 0', () => {
    renderChips({ messageCount: 0 });
    expect(screen.getByTestId('pmo-quick-actions')).toBeInTheDocument();
  });
});

describe('PMOQuickActions — chip send behavior', () => {
  it('"What changed today?" sends the correct message', () => {
    const onSend = jest.fn();
    renderChips({ onSend });
    fireEvent.click(screen.getByTestId('pmo-chip-changed_today'));
    expect(onSend).toHaveBeenCalledWith(
      'Summarize what changed in my projects in the last 24 hours.'
    );
  });

  it('"Show my blockers" sends the correct message', () => {
    const onSend = jest.fn();
    renderChips({ onSend });
    fireEvent.click(screen.getByTestId('pmo-chip-my_blockers'));
    expect(onSend).toHaveBeenCalledWith('What blockers do I currently own?');
  });

  it('"Create a new project" sends the correct message', () => {
    const onSend = jest.fn();
    renderChips({ onSend });
    fireEvent.click(screen.getByTestId('pmo-chip-create_project'));
    expect(onSend).toHaveBeenCalledWith('Help me create a new project.');
  });
});

describe('PMOQuickActions — client update inline picker', () => {
  it('opens project name input on "Client update for..."', () => {
    renderChips();
    fireEvent.click(screen.getByTestId('pmo-chip-client_update'));
    expect(screen.getByTestId('pmo-client-update-form')).toBeInTheDocument();
  });

  it('sends the correct message when project name is submitted', () => {
    const onSend = jest.fn();
    renderChips({ onSend });
    fireEvent.click(screen.getByTestId('pmo-chip-client_update'));
    fireEvent.change(screen.getByPlaceholderText('Project name...'), {
      target: { value: 'Henderson Q3' },
    });
    fireEvent.submit(screen.getByTestId('pmo-client-update-form'));
    expect(onSend).toHaveBeenCalledWith(
      'Generate a client update for "Henderson Q3" covering the last week.'
    );
  });

  it('does not send an empty project name', () => {
    const onSend = jest.fn();
    renderChips({ onSend });
    fireEvent.click(screen.getByTestId('pmo-chip-client_update'));
    fireEvent.submit(screen.getByTestId('pmo-client-update-form'));
    expect(onSend).not.toHaveBeenCalled();
  });
});

describe('PMOQuickActions — onboarding hint', () => {
  it('shows the onboarding hint on first visit', () => {
    renderChips();
    expect(screen.getByTestId('pmo-onboarding-hint')).toBeInTheDocument();
  });

  it('hides the hint after dismissal and persists to localStorage', () => {
    renderChips();
    fireEvent.click(screen.getByTestId('pmo-onboarding-dismiss'));
    expect(screen.queryByTestId('pmo-onboarding-hint')).not.toBeInTheDocument();
    expect(localStorage.getItem(`pmo_chat_v1.2_onboarding_dismissed:u_pm`)).toBe('1');
  });

  it('does not show the hint if already dismissed', () => {
    localStorage.setItem('pmo_chat_v1.2_onboarding_dismissed:u_pm', '1');
    renderChips();
    expect(screen.queryByTestId('pmo-onboarding-hint')).not.toBeInTheDocument();
  });
});
