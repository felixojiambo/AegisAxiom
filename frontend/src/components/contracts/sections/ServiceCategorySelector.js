import React from 'react';

/**
 * Service Category Selector Component
 * Allows users to choose between Field Services, Professional Services, Wholesale MRR, and Referral MRR
 */
export default function ServiceCategorySelector({
  serviceCategories,
  selectedCategory,
  onSelect,
  compact = false,
}) {
  return (
    <div>
      <label className={`${compact ? 'text-xs' : 'text-sm'} text-aegisaxiom-text-secondary block mb-2`}>
        Service Category *
      </label>
      <div className="grid grid-cols-2 gap-2">
        {Object.entries(serviceCategories).map(([key, cfg]) => {
          const Icon = cfg.icon;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onSelect(key)}
              className={`flex items-center gap-2 p-${compact ? '2' : '3'} rounded-lg border text-${compact ? 'xs' : 'sm'} transition ${
                selectedCategory === key
                  ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-500'
                  : 'border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover hover:text-aegisaxiom-text-secondary'
              }`}
            >
              <Icon size={compact ? 14 : 16} /> {cfg.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
