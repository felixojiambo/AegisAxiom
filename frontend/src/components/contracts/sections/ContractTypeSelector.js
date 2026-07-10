import React from 'react';

/**
 * Contract Type Selector Component
 * Displays available contract types based on the selected service category
 */
export default function ContractTypeSelector({
  serviceCategory,
  serviceCategories,
  typeConfig,
  selectedType,
  onSelect,
  compact = false,
  wholesaleProduct = '',
}) {
  const category = serviceCategories[serviceCategory];

  if (!category || category.types.length === 0) {
    return null;
  }

  return (
    <div>
      <label className={`${compact ? 'text-xs' : 'text-sm'} text-aegisaxiom-text-secondary block mb-2`}>
        {category.label === 'Wholesale MRR' ? 'Product *' : 'Contract Type *'}
      </label>
      <div className="grid grid-cols-3 gap-2">
        {category.types.map((typeKey) => {
          const cfg = typeConfig[typeKey];
          if (!cfg) return null;
          const Icon = cfg.icon;
          return (
            <button
              key={typeKey}
              type="button"
              onClick={() => onSelect(typeKey)}
              className={`flex items-center gap-2 p-${compact ? '1.5' : '2'} rounded-lg border text-xs transition ${
                selectedType === typeKey
                  ? cfg.color + ' border-current'
                  : 'border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
              }`}
            >
              <Icon size={14} /> {cfg.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
