/**
 * Resolves the correct backend API URL for both preview and production.
 *
 * IMPORTANT: Always use REACT_APP_BACKEND_URL from environment variables.
 * This ensures proper routing through Kubernetes ingress in production/preview.
 */
const API_URL = process.env.REACT_APP_BACKEND_URL || window.location.origin;
export default API_URL;
