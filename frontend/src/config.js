const FAMILY = Number(process.env.REACT_APP_PORT_FAMILY) || 9000;

export const BASE_URL =
  process.env.REACT_APP_BACKEND_URL ||
  `${window.location.protocol}//${window.location.hostname}:${FAMILY}`;