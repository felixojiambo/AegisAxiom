import React from 'react';

const SIZE_CLASS = {
  sm: 'h-5 w-5 text-[9px]',
  md: 'h-7 w-7 text-xs',
  lg: 'h-9 w-9 text-sm',
};

export default function UserAvatar({ user, size = 'md' }) {
  const sizeClass = SIZE_CLASS[size] ?? SIZE_CLASS.md;
  const initials = user
    ? (user.display_name ?? user.name ?? '?')
        .split(' ')
        .map((w) => w[0])
        .slice(0, 2)
        .join('')
        .toUpperCase()
    : '?';

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full bg-aegisaxiom-elevated border border-aegisaxiom-border font-semibold text-aegisaxiom-text-muted shrink-0 ${sizeClass}`}
      title={user?.display_name ?? user?.name ?? 'Unassigned'}
      data-testid={`user-avatar-${user?.user_id ?? user?._id ?? 'none'}`}
    >
      {user?.avatar_url ? (
        <img
          src={user.avatar_url}
          alt={user.display_name ?? ''}
          className={`rounded-full object-cover ${sizeClass}`}
        />
      ) : (
        initials
      )}
    </span>
  );
}
