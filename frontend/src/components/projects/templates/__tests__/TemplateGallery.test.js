/**
 * Unit tests for TemplateGallery component.
 *
 * Strategy: render with RTL and assert key interactive elements
 * (gallery cards, preview button, use button, edit/delete gating).
 *
 * Run with: cd frontend && yarn test --watchAll=false TemplateGallery
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

// TemplateGallery has no external deps that need mocking
import TemplateGallery from '../TemplateGallery';

const TEMPLATES = [
  {
    template_id: 'tmpl-1',
    name: 'UCaaS Deployment',
    description: 'Standard UCaaS rollout',
    project_type: 'waterfall',
    task_definitions: [{ title: 'Kick-off' }, { title: 'Design' }],
    use_count: 5,
    last_used_at: new Date(Date.now() - 86400_000 * 2).toISOString(), // 2 days ago
    is_system: false,
  },
  {
    _id: 'tmpl-2',
    name: 'Agile Sprint Kit',
    description: null,
    project_type: 'agile',
    task_definitions: [],
    use_count: 0,
    is_system: true,
  },
];

function renderGallery(overrides = {}) {
  const defaults = {
    templates: TEMPLATES,
    loading: false,
    canEdit: true,
    canDelete: true,
    onPreview: jest.fn(),
    onUse: jest.fn(),
    onEdit: jest.fn(),
    onDelete: jest.fn(),
  };
  return render(<TemplateGallery {...defaults} {...overrides} />);
}

describe('TemplateGallery', () => {
  it('renders a card for each template', () => {
    renderGallery();
    expect(screen.getByTestId('template-card-tmpl-1')).toBeInTheDocument();
    expect(screen.getByTestId('template-card-tmpl-2')).toBeInTheDocument();
    expect(screen.getByText('UCaaS Deployment')).toBeInTheDocument();
    expect(screen.getByText('Agile Sprint Kit')).toBeInTheDocument();
  });

  it('shows task count on each card', () => {
    renderGallery();
    expect(screen.getByText('2 tasks')).toBeInTheDocument();
    expect(screen.getByText('0 tasks')).toBeInTheDocument();
  });

  it('shows SYSTEM badge for system templates', () => {
    renderGallery();
    const systemBadges = screen.getAllByText('SYSTEM');
    expect(systemBadges.length).toBeGreaterThanOrEqual(1);
  });

  it('calls onPreview with the template id when Preview is clicked', () => {
    const onPreview = jest.fn();
    renderGallery({ onPreview });
    fireEvent.click(screen.getByTestId('template-preview-tmpl-1'));
    expect(onPreview).toHaveBeenCalledWith('tmpl-1');
  });

  it('calls onUse with the template id when Use is clicked', () => {
    const onUse = jest.fn();
    renderGallery({ onUse });
    fireEvent.click(screen.getByTestId('template-use-tmpl-1'));
    expect(onUse).toHaveBeenCalledWith('tmpl-1');
  });

  it('calls onEdit when edit button is clicked (canEdit=true)', () => {
    const onEdit = jest.fn();
    renderGallery({ onEdit });
    fireEvent.click(screen.getByTestId('template-edit-tmpl-1'));
    expect(onEdit).toHaveBeenCalledWith('tmpl-1');
  });

  it('does not show edit button when canEdit=false', () => {
    renderGallery({ canEdit: false });
    expect(screen.queryByTestId('template-edit-tmpl-1')).not.toBeInTheDocument();
  });

  it('calls onDelete for non-system templates (canDelete=true)', () => {
    const onDelete = jest.fn();
    renderGallery({ onDelete });
    fireEvent.click(screen.getByTestId('template-delete-tmpl-1'));
    expect(onDelete).toHaveBeenCalledWith('tmpl-1');
  });

  it('does not show delete button for system templates', () => {
    renderGallery();
    // tmpl-2 is is_system=true — delete button should be absent
    expect(screen.queryByTestId('template-delete-tmpl-2')).not.toBeInTheDocument();
  });

  it('shows loading skeleton when loading=true', () => {
    renderGallery({ loading: true });
    expect(screen.getByTestId('template-gallery-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('template-card-tmpl-1')).not.toBeInTheDocument();
  });

  it('shows empty state when templates list is empty', () => {
    renderGallery({ templates: [] });
    expect(screen.getByTestId('template-gallery-empty')).toBeInTheDocument();
  });

  it('truncates long descriptions to ~80 chars', () => {
    const longDesc = 'A'.repeat(100);
    renderGallery({
      templates: [{ ...TEMPLATES[0], description: longDesc }],
    });
    // Should contain truncated text ending in ellipsis
    const desc = screen.getByTitle(longDesc);
    expect(desc.textContent).toMatch(/…$/);
  });
});
