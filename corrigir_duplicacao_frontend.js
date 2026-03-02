// Função para remover duplicatas de forma mais eficiente
function removerDuplicatas(itens) {
  const itensUnicos = [];
  const vistos = new Set();
  
  itens.forEach(item => {
    // Chave mais específica: pedido + produto + quantidade + preço
    const chave = `${item.cd_pedido}_${item.idproduto}_${item.quantidade}_${item.precounitario}`;
    if (!vistos.has(chave)) {
      vistos.add(chave);
      itensUnicos.push(item);
    }
  });
  
  return itensUnicos;
}

// Função para agrupar itens por pedido
function agruparPorPedido(itens) {
  const itensPorPedido = {};
  
  itens.forEach(item => {
    const pedido = item.cd_pedido || 'sem-pedido';
    if (!itensPorPedido[pedido]) {
      itensPorPedido[pedido] = {
        dt_pedido: item.dt_pedido,
        cd_pedido: item.cd_pedido,
        itens: []
      };
    }
    
    // Remover duplicatas dentro do mesmo pedido
    const chaveItem = `${item.idproduto}_${item.quantidade}_${item.precounitario}`;
    const itemExiste = itensPorPedido[pedido].itens.some(existing => 
      `${existing.idproduto}_${existing.quantidade}_${existing.precounitario}` === chaveItem
    );
    
    if (!itemExiste) {
      itensPorPedido[pedido].itens.push(item);
    }
  });
  
  return itensPorPedido;
}

// Função principal para processar itens
function processarItens(itens) {
  // Primeiro remove duplicatas globais
  const itensSemDuplicatas = removerDuplicatas(itens);
  
  // Depois agrupa por pedido
  const itensAgrupados = agruparPorPedido(itensSemDuplicatas);
  
  return itensAgrupados;
}
