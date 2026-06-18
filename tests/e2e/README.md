## E2E - Fluxos Criticos

Este diretorio contem validacoes E2E com Playwright:

- `fluxo_abas.spec.js`
- `supervisor_dashboard.spec.js`
- `playwright.config.js`

### Pre-requisitos

1. Instalar dependencias de teste:
   - `npm i -D @playwright/test`
   - `npx playwright install`
2. Subir a aplicacao localmente em `http://127.0.0.1:5000` (ou ajustar `E2E_BASE_URL`).
3. Exportar credenciais de teste:
   - `E2E_USER`
   - `E2E_PASS`
   - opcional para perfil supervisor:
   - `E2E_SUPERVISOR_USER`
   - `E2E_SUPERVISOR_PASS`

### Execucao

`npx playwright test tests/e2e/fluxo_abas.spec.js --config=tests/e2e/playwright.config.js`

`npx playwright test tests/e2e/supervisor_dashboard.spec.js --config=tests/e2e/playwright.config.js`
