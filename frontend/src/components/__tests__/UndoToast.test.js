/**
 * UndoToast.test.js
 *
 * Tests countdown behavior, Undo click handler, and auto-dismiss.
 * Uses jest fake timers for the countdown bar and auto-dismiss delay.
 */

import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { toast } from 'sonner';
import { showUndoToast } from '../UndoToast';

// Mock sonner so we can capture what gets passed to toast.custom.
jest.mock('sonner', () => ({
  toast: {
    custom: jest.fn(),
    dismiss: jest.fn(),
  },
}));

// Helper: extract the rendered JSX from the first toast.custom call.
function renderLastToastContent() {
  const [[renderFn]] = toast.custom.mock.calls.slice(-1);
  // sonner passes the toast id to the render function
  const { container } = render(renderFn('test-id'));
  return container;
}

beforeEach(() => {
  jest.useFakeTimers();
  toast.custom.mockClear();
  toast.dismiss.mockClear();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('showUndoToast', () => {
  it('calls toast.custom with bottom-center position and correct duration', () => {
    showUndoToast({ message: 'Status changed', duration: 4000 });
    expect(toast.custom).toHaveBeenCalledTimes(1);
    const [, opts] = toast.custom.mock.calls[0];
    expect(opts.position).toBe('bottom-center');
    expect(opts.duration).toBe(4000);
  });

  it('renders the message text', () => {
    showUndoToast({ message: "Status changed to 'In Progress'" });
    const container = renderLastToastContent();
    expect(container.textContent).toContain("Status changed to 'In Progress'");
  });

  it('renders the Undo button when onUndo is provided', () => {
    showUndoToast({ message: 'Priority changed', onUndo: jest.fn() });
    renderLastToastContent();
    expect(screen.getByTestId('undo-toast-btn')).toBeInTheDocument();
  });

  it('does NOT render the Undo button when onUndo is omitted', () => {
    showUndoToast({ message: 'Cannot be undone' });
    renderLastToastContent();
    expect(screen.queryByTestId('undo-toast-btn')).not.toBeInTheDocument();
  });

  it('clicking Undo calls onUndo and dismisses after 1200ms', async () => {
    const onUndo = jest.fn().mockResolvedValue(undefined);
    showUndoToast({ message: 'Test', onUndo });
    renderLastToastContent();

    fireEvent.click(screen.getByTestId('undo-toast-btn'));

    await waitFor(() => expect(onUndo).toHaveBeenCalledTimes(1));

    // Dismiss should be called after 1200ms
    expect(toast.dismiss).not.toHaveBeenCalled();
    act(() => jest.advanceTimersByTime(1200));
    expect(toast.dismiss).toHaveBeenCalledWith('test-id');
  });

  it('shows "Undone." text after Undo is clicked', async () => {
    const onUndo = jest.fn().mockResolvedValue(undefined);
    showUndoToast({ message: 'Test', onUndo });
    renderLastToastContent();

    fireEvent.click(screen.getByTestId('undo-toast-btn'));

    await waitFor(() => expect(screen.getByTestId('undo-toast')).toHaveTextContent('Undone.'));
  });

  it('renders a progress bar element', () => {
    showUndoToast({ message: 'Test', onUndo: jest.fn() });
    renderLastToastContent();
    expect(screen.getByTestId('undo-toast-progress')).toBeInTheDocument();
  });
});
