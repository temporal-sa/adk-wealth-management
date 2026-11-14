// The API is served from the same origin as the web app: in the container image the API serves
// the built app, and locally the React dev server proxies /api to it (see "proxy" in package.json).
export const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || '/api';
