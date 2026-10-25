import axios from 'axios';

const api = axios.create({
  // Vercel routes browser requests through the shared public /api prefix.
  baseURL: import.meta.env.MODE === 'vercel' ? '/' : (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'),
  timeout: 10000,
});

export default api;
