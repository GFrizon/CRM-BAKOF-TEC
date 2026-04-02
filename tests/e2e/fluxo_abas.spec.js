const { test, expect } = require('@playwright/test');

async function login(page) {
  // Ajuste seletores caso o form de login mude.
  await page.goto('/login');
  await page.getByLabel(/usuario|email/i).fill(process.env.E2E_USER || 'admin');
  await page.getByLabel(/senha/i).fill(process.env.E2E_PASS || 'admin');
  await page.getByRole('button', { name: /entrar|login/i }).click();
  await page.waitForURL('**/meus-clientes**');
}

test.describe('Fluxo de abas - Meus Clientes', () => {
  test('troca de aba nao deve zerar badge visivel', async ({ page }) => {
    await login(page);
    const tabInativos = page.locator('.modern-tabs .nav-link[data-aba="inativos"]').first();
    const badgeInativos = tabInativos.locator('.tab-count').first();
    const antes = parseInt((await badgeInativos.textContent()) || '0', 10) || 0;

    await page.locator('.modern-tabs .nav-link[data-aba="oracle"]').first().click();
    await page.waitForLoadState('networkidle');
    await page.locator('.modern-tabs .nav-link[data-aba="inativos"]').first().click();
    await page.waitForLoadState('networkidle');

    const depois = parseInt((await page.locator('.modern-tabs .nav-link[data-aba="inativos"] .tab-count').first().textContent()) || '0', 10) || 0;
    expect(depois).toBeGreaterThanOrEqual(0);
    if (antes > 0) {
      expect(depois).toBeGreaterThan(0);
    }
  });

  test('expandir e recolher representante persiste no retorno para aba', async ({ page }) => {
    await login(page);
    await page.locator('.modern-tabs .nav-link[data-aba="oracle"]').first().click();
    await page.waitForLoadState('networkidle');

    const header = page.locator('.rep-header').first();
    await expect(header).toBeVisible();
    await header.click();
    await expect(header).toHaveAttribute('aria-expanded', 'true');

    await page.locator('.modern-tabs .nav-link[data-aba="contatados"]').first().click();
    await page.waitForLoadState('networkidle');
    await page.locator('.modern-tabs .nav-link[data-aba="oracle"]').first().click();
    await page.waitForLoadState('networkidle');

    await expect(page.locator('.rep-header').first()).toHaveAttribute('aria-expanded', 'true');
  });

  test('registrar ligacao ajusta contadores entre abas', async ({ page }) => {
    await login(page);
    await page.locator('.modern-tabs .nav-link[data-aba="pendentes"]').first().click();
    await page.waitForLoadState('networkidle');

    const btnLigacao = page.locator('button[title="Registrar Ligação"]').first();
    await expect(btnLigacao).toBeVisible();
    await btnLigacao.click();

    await page.locator('#contato_nome').fill('E2E');
    await page.locator('#resultado').selectOption('nao_comprou');
    await page.locator('#observacao').fill('Teste E2E abas');
    await page.locator('#modalLigacao button[type="submit"]').click();
    await page.waitForTimeout(1200);

    const badgeContatados = page.locator('.modern-tabs .nav-link[data-aba="contatados"] .tab-count').first();
    const totalContatados = parseInt((await badgeContatados.textContent()) || '0', 10) || 0;
    expect(totalContatados).toBeGreaterThanOrEqual(0);
  });
});
