// Centralized env config with normalization
const rawBase = import.meta.env.VITE_BASE_URL;

export const BASE_URL = rawBase ? rawBase.replace(/\/+$/, '') : '';
export const SITENAME = import.meta.env.VITE_SITENAME || 'FilmsClub';
export const TG_URL = import.meta.env.VITE_TG_URL || '';
export const TG_USERNAME = import.meta.env.VITE_TG_USERNAME || '';
export const API_URL = import.meta.env.VITE_API_URL || '';
export const API_KEY = import.meta.env.VITE_API_KEY || '';
export const SITEKEY = import.meta.env.VITE_SITEKEY || '';
export const WORKERURL = import.meta.env.VITE_WORKERURL || '';
