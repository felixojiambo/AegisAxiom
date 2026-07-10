// ESLint 9+ flat config — migrated from CRA's implicit eslint-config-react-app defaults.
// Packages already listed in devDependencies: @eslint/js, eslint-plugin-react,
// eslint-plugin-react-hooks, eslint-plugin-jsx-a11y, eslint-plugin-import,
// eslint-config-prettier, globals.
//
// NOTE: this file uses CommonJS require() because package.json does not set
// "type": "module". Add that field if you want to switch to ESM imports here.

const js = require('@eslint/js');
const reactPlugin = require('eslint-plugin-react');
const reactHooksPlugin = require('eslint-plugin-react-hooks');
const jsxA11yPlugin = require('eslint-plugin-jsx-a11y');
const importPlugin = require('eslint-plugin-import');
const prettierConfig = require('eslint-config-prettier');
const globals = require('globals');

module.exports = [
  // --- Base JS recommended rules ---
  js.configs.recommended,

  // --- React + JSX (all src files) ---
  {
    files: ['src/**/*.{js,jsx}'],
    plugins: {
      react: reactPlugin,
      'react-hooks': reactHooksPlugin,
      'jsx-a11y': jsxA11yPlugin,
      import: importPlugin,
    },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.es2021,
        ...globals.node,
      },
    },
    settings: {
      react: {
        version: 'detect',
      },
      'import/resolver': {
        node: { extensions: ['.js', '.jsx'] },
      },
    },
    rules: {
      // React rules
      ...reactPlugin.configs.recommended.rules,
      'react/react-in-jsx-scope': 'off', // Not needed with React 17+ JSX transform
      'react/prop-types': 'off',          // Project does not use PropTypes
      'react/display-name': 'off',

      // React Hooks rules
      ...reactHooksPlugin.configs.recommended.rules,

      // Accessibility — warn to avoid blocking CI on pre-existing violations
      'jsx-a11y/alt-text': 'warn',
      'jsx-a11y/anchor-is-valid': 'warn',
      'jsx-a11y/no-autofocus': 'off',    // autoFocus used intentionally in modals

      // Import hygiene
      'import/no-duplicates': 'error',

      // General JS quality
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'no-debugger': 'error',
      'prefer-const': 'warn',
    },
  },

  // --- Test files — relax some rules ---
  {
    files: ['src/**/*.test.{js,jsx}', 'src/**/__tests__/**/*.{js,jsx}'],
    languageOptions: {
      globals: { ...globals.jest },
    },
    rules: {
      'no-unused-vars': 'off',
    },
  },

  // --- Prettier compat (disables formatting rules that conflict with prettier) ---
  prettierConfig,

  // --- Ignored paths ---
  {
    ignores: [
      'node_modules/**',
      'build/**',
      'public/**',
      'coverage/**',
      'plugins/**',
    ],
  },
];
