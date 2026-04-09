const { test, expect } = require('@playwright/test');

async function loginSupervisor(page) {
  await page.goto('/login');
  await page.getByLabel(/usuario|email/i).fill(
    process.env.E2E_SUPERVISOR_USER || process.env.E2E_USER || 'admin'
  );
  await page.getByLabel(/senha/i).fill(
    process.env.E2E_SUPERVISOR_PASS || process.env.E2E_PASS || 'admin'
  );
  await page.getByRole('button', { name: /entrar|login/i }).click();
}

test.describe('Dashboard Supervisor - Consistencia de Uso', () => {
  test('deve alternar entre consultores e televendas sem perder contexto visual', async ({ page }) => {
    await loginSupervisor(page);
    await page.goto('/supervisor');
    await page.waitForLoadState('networkidle');

    await expect(page.getByRole('link', { name: /consultores/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /televendas/i })).toBeVisible();
    await expect(page.getByText(/Sem Pedido 90-150d/i)).toBeVisible();
    await expect(page.getByText(/Proximos Inativacao/i)).toBeVisible();
    await expect(page.getByText(/Clientes Inativos/i)).toHaveCount(0);

    await page.getByRole('link', { name: /televendas/i }).click();
    await page.waitForURL('**/supervisor/televendas**');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText(/Clientes Inativos/i)).toBeVisible();
    await expect(page.getByText(/Sem Pedido 90-150d/i)).toHaveCount(0);
    await expect(page.getByText(/Proximos Inativacao/i)).toHaveCount(0);
  });

  test('card Total de Clientes deve permanecer visivel nos dois mundos', async ({ page }) => {
    await loginSupervisor(page);
    await page.goto('/supervisor');
    await page.waitForLoadState('networkidle');
    await expect(page.getByText(/Total de Clientes/i)).toBeVisible();

    await page.getByRole('link', { name: /televendas/i }).click();
    await page.waitForURL('**/supervisor/televendas**');
    await page.waitForLoadState('networkidle');
    await expect(page.getByText(/Total de Clientes/i)).toBeVisible();
  });
});
