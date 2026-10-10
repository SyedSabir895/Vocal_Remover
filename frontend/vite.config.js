import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Fixed port: the backend only accepts requests from this origin.
  server: { port: 5180, strictPort: true },
})
