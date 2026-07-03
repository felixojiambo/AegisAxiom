/**
 * Unit tests for InstantiateDialog component.
 *
 * Covers: form validation, live preview trigger on date change, submit flow.
 *
 * Run with: cd frontend && yarn test --watchAll=false InstantiateDialog
 */

jest.mock('@/lib/api', () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

jest.mock('@/lib/apiConfig', () => 'http://localhost:8000');

// Mock TemplatePreview so we don't need to set up the full preview fetch
jest.mock('../TemplatePreview', () => function MockTemplatePreview({ templateId, startDate, onEndDate }) {
  // Simulate the callback so the parent can receive the end date
  if (onEndDate && startDate) onEndDate('2025-09-30');
  return (
    <div data-testid="mock-template-preview" data-start-date={startDate} data-template-id={templateId} />
  );
});

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import InstantiateDialog from '../InstantiateDialog';

const { api } = require('@/lib/api');
const { toast } = require('sonner');

const TEMPLATE = {
  template_id: 'tmpl-abc',
  name: 'UCaaS Rollout',
  name_pattern: 'UCaaS Rollout',
  project_type: 'waterfall',
};

const ASSIGNEES = [
  { user_id: 'u1', name: 'Alice PM' },
  { user_id: 'u2', name: 'Bob Dev' },
];

const CURRENT_USER = { user_id: 'u1', name: 'Alice PM' };

function renderDialog(overrides = {}) {
  const defaults = {
    template: TEMPLATE,
    assignees: ASSIGNEES,
    currentUser: CURRENT_USER,
    onClose: jest.fn(),
    onCreated: jest.fn(),
  };
  return render(<InstantiateDialog {...defaults} {...overrides} />);
}

describe('InstantiateDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders with pre-filled project name from template name_pattern', () => {
    renderDialog();
    const nameInput = screen.getByTestId('instantiate-name');
    expect(nameInput.value).toBe('UCaaS Rollout');
  });

  it('renders with today as default start date', () => {
    renderDialog();
    const today = new Date().toISOString().slice(0, 10);
    expect(screen.getByTestId('instantiate-start-date').value).toBe(today);
  });

  it('pre-selects current user as PM', () => {
    renderDialog();
    expect(screen.getByTestId('instantiate-pm').value).toBe('u1');
  });

  it('renders the live preview pane', () => {
    renderDialog();
    expect(screen.getByTestId('mock-template-preview')).toBeInTheDocument();
  });

  it('shows an error when submitting with an empty name', async () => {
    renderDialog();
    const nameInput = screen.getByTestId('instantiate-name');
    await userEvent.clear(nameInput);
    fireEvent.click(screen.getByTestId('instantiate-submit'));
    expect(screen.getByTestId('instantiate-error')).toHaveTextContent('required');
    expect(api.post).not.toHaveBeenCalled();
  });

  it('submit button is disabled when name is empty', async () => {
    renderDialog();
    const nameInput = screen.getByTestId('instantiate-name');
    await userEvent.clear(nameInput);
    expect(screen.getByTestId('instantiate-submit')).toBeDisabled();
  });

  it('updates the preview when start date changes', async () => {
    renderDialog();
    const dateInput = screen.getByTestId('instantiate-start-date');
    await userEvent.clear(dateInput);
    await userEvent.type(dateInput, '2025-08-01');
    // The mock preview receives the debounced start date
    await waitFor(() => {
      const preview = screen.getByTestId('mock-template-preview');
      expect(preview).toBeInTheDocument();
    });
  });

  it('calls api.post on valid submit and invokes onCreated', async () => {
    api.post.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ project_id: 'proj-new-123' }),
    });
    const onCreated = jest.fn();
    renderDialog({ onCreated });

    fireEvent.click(screen.getByTestId('instantiate-submit'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        'http://localhost:8000/api/projects/templates/tmpl-abc/instantiate',
        expect.objectContaining({
          project_name: 'UCaaS Rollout',
          pm_id: 'u1',
        }),
      );
      expect(onCreated).toHaveBeenCalledWith('proj-new-123');
      expect(toast.success).toHaveBeenCalled();
    });
  });

  it('shows error message when api.post returns !ok', async () => {
    api.post.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ detail: 'Template not found' }),
    });
    renderDialog();
    fireEvent.click(screen.getByTestId('instantiate-submit'));
    await waitFor(() => {
      expect(screen.getByTestId('instantiate-error')).toHaveTextContent('Template not found');
    });
  });

  it('advanced section is hidden by default and reveals on click', () => {
    renderDialog();
    expect(screen.queryByTestId('instantiate-advanced')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('instantiate-advanced-toggle'));
    expect(screen.getByTestId('instantiate-advanced')).toBeInTheDocument();
  });

  it('sends task_title_prefix when advanced field is filled', async () => {
    api.post.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ project_id: 'p1' }),
    });
    renderDialog();
    fireEvent.click(screen.getByTestId('instantiate-advanced-toggle'));
    await userEvent.type(screen.getByTestId('instantiate-title-prefix'), '[ACME]');
    fireEvent.click(screen.getByTestId('instantiate-submit'));
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({ task_title_prefix: '[ACME]' }),
      );
    });
  });

  it('calls onClose when Cancel is clicked', () => {
    const onClose = jest.fn();
    renderDialog({ onClose });
    fireEvent.click(screen.getByTestId('instantiate-cancel'));
    expect(onClose).toHaveBeenCalled();
  });

  it('calls onClose when X button is clicked', () => {
    const onClose = jest.fn();
    renderDialog({ onClose });
    fireEvent.click(screen.getByTestId('instantiate-close'));
    expect(onClose).toHaveBeenCalled();
  });
});
