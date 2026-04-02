# Supervisor de Representante - Guia de Uso

## Visão Geral

O tipo de usuário **Supervisor de Representante** (`supervisor_repr`) foi criado para permitir que supervisores visualizem clientes de representantes específicos sem permissão para modificar dados.

## Características

### Permissões
- ✅ **Visualizar** clientes dos representantes vinculados
- ✅ **Acessar** todas as abas (pendentes, oracle, inativos, próximos à inativação)
- ✅ **Ver** detalhes de clientes Oracle
- ✅ **Ver** histórico de ligações e notas
- ❌ **Não pode** registrar ligações
- ❌ **Não pode** adicionar ou editar notas
- ❌ **Não pode** editar dados de clientes
- ❌ **Não pode** importar clientes

### Vínculos com Representantes

Os vínculos entre supervisores e representantes são armazenados de duas formas:

1. **Manual**: Administrador adiciona códigos de representantes manualmente
2. **TG 650 (Oracle)**: Sincronização automática da tabela `Geelemen` (cd_tg = 650)

## Como Usar

### 1. Criar Usuário Supervisor de Representante

**Acesso**: Apenas usuários do tipo `supervisor`

1. Acesse: `/supervisor/supervisores-representante`
2. Clique em **"Novo Supervisor"**
3. Preencha:
   - Nome
   - Email
   - Senha
   - Código Supervisor TG650 (opcional, necessário para sincronização)
4. Clique em **"Salvar"**

### 2. Gerenciar Vínculos de Representantes

**Opção A: Adicionar Manualmente**

1. Na lista de supervisores, clique em **"Vínculos"**
2. Clique em **"Adicionar Vínculo"**
3. Informe:
   - Código do Representante (obrigatório)
   - Nome do Representante (opcional)
4. Clique em **"Adicionar"**

**Opção B: Sincronizar da TG 650**

1. Configure o **Código Supervisor TG650** no cadastro do usuário
2. Na tela de vínculos, clique em **"Sincronizar TG650"**
3. O sistema buscará automaticamente os representantes vinculados no Oracle

### 3. Visualização de Clientes

Quando um usuário `supervisor_repr` acessa `/meus-clientes`:

- **Filtro Automático**: Apenas clientes dos representantes vinculados são exibidos
- **Todas as Abas**: Acesso a pendentes, oracle, inativos, próximos à inativação
- **Modo Leitura**: Botões de ação (registrar ligação, adicionar nota) não aparecem

## Estrutura Técnica

### Banco de Dados

**Tabela: `supervisor_representante_vinculos`**

```sql
CREATE TABLE supervisor_representante_vinculos (
    id INT PRIMARY KEY AUTO_INCREMENT,
    supervisor_id INT NOT NULL,
    codigo_representante VARCHAR(50) NOT NULL,
    nome_representante VARCHAR(200),
    ativo BOOLEAN DEFAULT TRUE,
    data_cadastro DATETIME DEFAULT CURRENT_TIMESTAMP,
    sincronizado_tg650 BOOLEAN DEFAULT FALSE,
    codigo_supervisor_tg650 VARCHAR(20),
    UNIQUE KEY (supervisor_id, codigo_representante),
    FOREIGN KEY (supervisor_id) REFERENCES usuarios(id)
);
```

**Campo Adicional em `usuarios`:**
- `codigo_supervisor_tg650`: Código do supervisor na TG 650 do Oracle

### Oracle - TG 650

A tabela `Geelemen` com `cd_tg = 650` armazena vínculos:

- **categoria**: Código do supervisor
- **elemento**: Código do representante
- **desc_categoria**: Descrição do supervisor (via `Gecatego`)

**Query de Sincronização:**

```sql
SELECT 
    TG650.categoria,
    TG650.elemento as cd_representante,
    CAT.desc_categoria,
    REP.nome_completo as nome_representante
FROM Geelemen TG650
LEFT JOIN Gecatego CAT ON CAT.cd_tg = 650 AND CAT.categoria = TG650.categoria
LEFT JOIN geempres REP ON REP.cd_empresa = TG650.elemento
WHERE TG650.cd_tg = 650
  AND TG650.categoria = :codigo_supervisor
ORDER BY TG650.categoria, TG650.elemento
```

## Rotas da API

### Gerenciamento de Supervisores

- `GET /supervisor/supervisores-representante` - Listar supervisores
- `GET /supervisor/supervisores-representante/<id>/vinculos` - Listar vínculos
- `POST /supervisor/supervisores-representante/<id>/vinculos/adicionar` - Adicionar vínculo
- `POST /supervisor/supervisores-representante/<id>/vinculos/<vinculo_id>/remover` - Remover vínculo
- `POST /supervisor/supervisores-representante/<id>/sincronizar-tg650` - Sincronizar TG650

### Visualização (Somente Leitura)

- `GET /meus-clientes` - Lista de clientes (filtrada por representantes)
- `GET /detalhes-cliente-oracle/<id>` - Detalhes do cliente
- `GET /clientes/<id>/notas` - Listar notas (somente leitura)

### Bloqueadas (403 Forbidden)

- `POST /registrar-ligacao/<id>` - ❌ Bloqueado
- `POST /clientes/<id>/notas` - ❌ Bloqueado

## Migração do Banco de Dados

**Aplicar Migration:**

```bash
mysql -u usuario -p nome_banco < migrations/add_supervisor_repr.sql
```

**Reverter (se necessário):**

```bash
mysql -u usuario -p nome_banco < migrations/rollback_supervisor_repr.sql
```

## Fluxo de Trabalho Típico

1. **Administrador** cria usuário `supervisor_repr`
2. **Administrador** configura código TG650 ou adiciona vínculos manualmente
3. **Administrador** sincroniza vínculos da TG 650 (se aplicável)
4. **Supervisor de Representante** faz login
5. **Supervisor de Representante** acessa `/meus-clientes`
6. **Sistema** filtra automaticamente apenas clientes dos representantes vinculados
7. **Supervisor de Representante** visualiza dados, histórico e detalhes (modo somente leitura)

## Segurança

- ✅ Validação de permissões no backend (não apenas frontend)
- ✅ Filtros aplicados em todas as abas
- ✅ Bloqueio de rotas de escrita com HTTP 403
- ✅ Vínculos armazenados localmente para performance
- ✅ Sincronização opcional com Oracle TG 650

## Troubleshooting

### Supervisor não vê nenhum cliente

**Causa**: Nenhum representante vinculado

**Solução**: 
1. Verifique se há vínculos ativos em `/supervisor/supervisores-representante`
2. Adicione vínculos manualmente ou sincronize da TG 650

### Erro ao sincronizar TG 650

**Causa**: Código TG650 não configurado ou inexistente no Oracle

**Solução**:
1. Verifique se o campo `codigo_supervisor_tg650` está preenchido
2. Confirme que o código existe na tabela `Geelemen` (cd_tg = 650)

### Clientes aparecem mas não é possível registrar ligação

**Comportamento esperado**: Usuários `supervisor_repr` têm acesso somente leitura

**Solução**: Se precisar registrar ligações, altere o tipo do usuário para `consultor` ou `televendas`

## Manutenção

### Adicionar Novo Representante

1. Acesse a tela de vínculos do supervisor
2. Clique em "Adicionar Vínculo"
3. Informe o código do representante

### Remover Representante

1. Acesse a tela de vínculos do supervisor
2. Clique no botão de remover (ícone de lixeira)
3. O vínculo será marcado como inativo

### Atualizar Vínculos da TG 650

1. Acesse a tela de vínculos do supervisor
2. Clique em "Sincronizar TG650"
3. Novos vínculos serão adicionados, existentes serão atualizados

## Notas Técnicas

- **Performance**: Vínculos são armazenados localmente (MySQL) para evitar consultas Oracle em cada requisição
- **Cache**: Lista de representantes vinculados é carregada uma vez por sessão
- **Filtros**: Aplicados no backend usando o código do representante extraído do campo `representante` (formato: "NOME - CODIGO")
- **Compatibilidade**: Funciona com todas as abas existentes (pendentes, oracle, inativos, próximos)
