## E2E - Fluxo de Abas

Este diretório contém um esqueleto inicial de validação E2E com Playwright:

- `fluxo_abas.spec.js`
- `playwright.config.js`

### Pré-requisitos

1. Instalar dependências de teste:
   - `npm i -D @playwright/test`
   - `npx playwright install`
2. Subir a aplicação localmente em `http://127.0.0.1:5000` (ou ajustar `E2E_BASE_URL`).
3. Exportar credenciais de teste:
   - `E2E_USER`
   - `E2E_PASS`

### Execução

`npx playwright test tests/e2e/fluxo_abas.spec.js --config=tests/e2e/playwright.config.js`

