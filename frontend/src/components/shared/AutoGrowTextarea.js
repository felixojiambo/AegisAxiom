import React, { useEffect, useRef } from 'react';

/**
 * Textarea that auto-grows to fit its content.
 * Pass all normal textarea props (value, onChange, className, placeholder, rows, etc).
 * The `rows` prop sets the minimum height.
 */
export default function AutoGrowTextarea({ value, className = '', ...props }) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    }
  }, [value]);

  return (
    <textarea
      ref={ref}
      value={value}
      className={`${className} overflow-hidden`}
      {...props}
    />
  );
}
