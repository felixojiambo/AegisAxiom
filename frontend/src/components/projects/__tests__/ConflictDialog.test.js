/**
 * Tests for ConflictDialog component.
 *
 * Verifies: rendering, diff display, and each of the 3 button actions.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import ConflictDialog from '../widgets/ConflictDialog';

// lucide-react sometimes needs a mock in CRA test environments.
jest.mock('lucide-react', () => ({
  AlertTriangle: ({ size, ...rest }) => <svg data-testid="icon-alert" {...rest} />,
}));

const MY_DOC = {
  task_id: 'task-1',
  title: 'My Title',
  status: 'in_progress',
  priority: 'high',
  updated_at: '2026-05-11T10:00:00Z',
};

const THEIR_DOC = {
  task_id: 'task-1',
  title: 'Their Title',
  status: 'blocked',
  priority: 'high',       // same — should NOT appear in diff
  updated_at: '2026-05-11T10:01:00Z',
};

describe('ConflictDialog — rendering', () => {
  it('does not render when isOpen=false', () => {
    render(<ConflictDialog isOpen={false} myDoc={MY_DOC} theirDoc={THEIR_DOC} />);
    expect(screen.queryByTestId('conflict-dialog')).toBeNull();
  });

  it('renders the dialog when isOpen=true', () => {
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={jest.fn()} onOverwrite={jest.fn()} onMerge={jest.fn()} />);
    expect(screen.getByTestId('conflict-dialog')).toBeTruthy();
  });

  it('shows the conflict heading', () => {
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={jest.fn()} onOverwrite={jest.fn()} onMerge={jest.fn()} />);
    expect(screen.getByText(/editing conflict/i)).toBeTruthy();
  });

  it('shows changed fields side-by-side (title and status differ)', () => {
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={jest.fn()} onOverwrite={jest.fn()} onMerge={jest.fn()} />);
    expect(screen.getByText('My Title')).toBeTruthy();
    expect(screen.getByText('Their Title')).toBeTruthy();
    expect(screen.getByText('in_progress')).toBeTruthy();
    expect(screen.getByText('blocked')).toBeTruthy();
  });

  it('does not show priority in diff when values are equal', () => {
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={jest.fn()} onOverwrite={jest.fn()} onMerge={jest.fn()} />);
    // 'high' appears once (priority is same, so not in diff — only in the docs themselves, not in the rendered diff)
    const highCells = screen.queryAllByText('high');
    // Should be 0 since priority is unchanged and thus filtered from changedFields.
    expect(highCells).toHaveLength(0);
  });

  it('renders all 3 action buttons', () => {
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={jest.fn()} onOverwrite={jest.fn()} onMerge={jest.fn()} />);
    expect(screen.getByTestId('conflict-discard')).toBeTruthy();
    expect(screen.getByTestId('conflict-merge')).toBeTruthy();
    expect(screen.getByTestId('conflict-overwrite')).toBeTruthy();
  });
});

describe('ConflictDialog — button actions', () => {
  it('calls onDiscard when "Discard mine" is clicked', () => {
    const onDiscard = jest.fn();
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={onDiscard} onOverwrite={jest.fn()} onMerge={jest.fn()} />);
    fireEvent.click(screen.getByTestId('conflict-discard'));
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it('calls onOverwrite when "Overwrite theirs" is clicked', () => {
    const onOverwrite = jest.fn();
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={jest.fn()} onOverwrite={onOverwrite} onMerge={jest.fn()} />);
    fireEvent.click(screen.getByTestId('conflict-overwrite'));
    expect(onOverwrite).toHaveBeenCalledTimes(1);
  });

  it('calls onMerge when "Merge manually" is clicked', () => {
    const onMerge = jest.fn();
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={jest.fn()} onOverwrite={jest.fn()} onMerge={onMerge} />);
    fireEvent.click(screen.getByTestId('conflict-merge'));
    expect(onMerge).toHaveBeenCalledTimes(1);
  });

  it('does not auto-resolve — all 3 buttons require user action', () => {
    const onDiscard = jest.fn();
    const onOverwrite = jest.fn();
    const onMerge = jest.fn();
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={onDiscard} onOverwrite={onOverwrite} onMerge={onMerge} />);
    // None called yet — dialog stays open until user clicks.
    expect(onDiscard).not.toHaveBeenCalled();
    expect(onOverwrite).not.toHaveBeenCalled();
    expect(onMerge).not.toHaveBeenCalled();
  });
});

describe('ConflictDialog — edge cases', () => {
  it('shows fallback message when no changed fields can be computed', () => {
    const sameDoc = { task_id: 'task-1', status: 'scoped' };
    render(<ConflictDialog isOpen myDoc={sameDoc} theirDoc={sameDoc} onDiscard={jest.fn()} onOverwrite={jest.fn()} onMerge={jest.fn()} />);
    expect(screen.getByText(/conflicting fields could not be determined/i)).toBeTruthy();
  });

  it('renders a backdrop overlay', () => {
    render(<ConflictDialog isOpen myDoc={MY_DOC} theirDoc={THEIR_DOC} onDiscard={jest.fn()} onOverwrite={jest.fn()} onMerge={jest.fn()} />);
    // The aria-hidden backdrop div should be present.
    const backdrop = document.querySelector('[aria-hidden="true"]');
    expect(backdrop).toBeTruthy();
  });
});
