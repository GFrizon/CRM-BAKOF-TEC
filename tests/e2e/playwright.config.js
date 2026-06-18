// Config basico para validar o fluxo de abas em ambiente local.
// Exemplo de execucao:
//   npx playwright test tests/e2e/fluxo_abas.spec.js
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './',
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5000',
    headless: true,
  },
});

