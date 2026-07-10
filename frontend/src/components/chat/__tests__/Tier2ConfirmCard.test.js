/**
 * Tier2ConfirmCard.test.js
 *
 * Tests that the card renders with tool name + params, and that
 * Confirm / Cancel send the correct messages.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import Tier2ConfirmCard from '../Tier2ConfirmCard';

const defaultProps = {
  tool: 'bulk_update_task_status',
  intendedParams: { status: 'in_progress', task_count: 5 },
  detail: 'This will affect 5 tasks.',
  onSend: jest.fn(),
};

beforeEach(() => {
  defaultProps.onSend.mockClear();
});

describe('Tier2ConfirmCard', () => {
  it('renders the pretty tool name', () => {
    render(<Tier2ConfirmCard {...defaultProps} />);
    expect(screen.getByTestId('tier2-confirm-card')).toBeInTheDocument();
    expect(screen.getByText(/change status on multiple tasks/i)).toBeInTheDocument();
  });

  it('renders "Confirmation needed" header', () => {
    render(<Tier2ConfirmCard {...defaultProps} />);
    expect(screen.getByText(/confirmation needed/i)).toBeInTheDocument();
  });

  it('renders intended_params as a summary list', () => {
    render(<Tier2ConfirmCard {...defaultProps} />);
    expect(screen.getByText('in_progress')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('renders the detail string when provided', () => {
    render(<Tier2ConfirmCard {...defaultProps} />);
    expect(screen.getByText('This will affect 5 tasks.')).toBeInTheDocument();
  });

  it('Confirm button calls onSend with "Yes, proceed."', () => {
    render(<Tier2ConfirmCard {...defaultProps} />);
    fireEvent.click(screen.getByTestId('tier2-confirm-btn'));
    expect(defaultProps.onSend).toHaveBeenCalledWith('Yes, proceed.');
  });

  it('Cancel button calls onSend with "No, don\'t do that."', () => {
    render(<Tier2ConfirmCard {...defaultProps} />);
    fireEvent.click(screen.getByTestId('tier2-cancel-btn'));
    expect(defaultProps.onSend).toHaveBeenCalledWith("No, don't do that.");
  });

  it('shows resolved state after Confirm is clicked', () => {
    render(<Tier2ConfirmCard {...defaultProps} />);
    fireEvent.click(screen.getByTestId('tier2-confirm-btn'));
    expect(screen.getByTestId('tier2-confirm-resolved')).toBeInTheDocument();
    expect(screen.getByText(/confirmed.*proceeding/i)).toBeInTheDocument();
  });

  it('shows resolved state after Cancel is clicked', () => {
    render(<Tier2ConfirmCard {...defaultProps} />);
    fireEvent.click(screen.getByTestId('tier2-cancel-btn'));
    expect(screen.getByTestId('tier2-confirm-resolved')).toBeInTheDocument();
    expect(screen.getByText(/cancelled/i)).toBeInTheDocument();
  });

  it('falls back gracefully for unknown tool names', () => {
    render(<Tier2ConfirmCard tool="some_unknown_tool" onSend={jest.fn()} />);
    expect(screen.getByText(/some_unknown_tool/i)).toBeInTheDocument();
  });

  it('renders without intendedParams or detail without crashing', () => {
    render(<Tier2ConfirmCard tool="create_task" onSend={jest.fn()} />);
    expect(screen.getByTestId('tier2-confirm-card')).toBeInTheDocument();
  });

  it('maps all known tool names correctly', () => {
    const cases = [
      ['create_project', 'Create a new project'],
      ['create_task', 'Create a new task'],
      ['delete_task', 'Delete a task'],
      ['bulk_update_task_status', 'Change status on multiple tasks'],
      ['bulk_assign_tasks', 'Reassign multiple tasks'],
      ['create_project_from_template', 'Create a project from a template'],
    ];
    cases.forEach(([tool, expected]) => {
      const { unmount } = render(<Tier2ConfirmCard tool={tool} onSend={jest.fn()} />);
      expect(screen.getByText(new RegExp(expected, 'i'))).toBeInTheDocument();
      unmount();
    });
  });
});
