from __future__ import annotations

from typing import Any, Sequence

from routes.clientes_ligacoes.listagem_filters import (
    corresponde_conceito_filtro,
    corresponde_consultor_filtro,
    corresponde_termo_busca,
)
from routes.clientes_ligacoes.listagem_grouping_utils import consolidar_dados_grupos


def filtrar_projecao_agrupada(
    representantes_ordenados_base: Sequence[Sequence[Any]],
    *,
    conceito_filtro: str,
    consultor_filtro: str,
    termo: str,
    campos_busca: Sequence[str],
    chave_sem_grupo: str,
    conceitos_sem_conceito: Sequence[Any],
) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, str]], int, dict[str, Any]]:
    representantes_data: dict[str, dict[str, Any]] = {}
    for grupo_base in representantes_ordenados_base or []:
        if not isinstance(grupo_base, (list, tuple)) or len(grupo_base) < 2:
            continue
        nome_grupo = str(grupo_base[0] or "").strip()
        dados_grupo = grupo_base[1] or {}
        clientes_filtrados = []
        for cliente in (dados_grupo.get("clientes") or []):
            conceito_cliente = str(cliente.get("conceito") or "").strip().upper()
            consultor_cliente = str(cliente.get("categoria_consultor") or "").strip()
            if not corresponde_conceito_filtro(conceito_filtro, conceito_cliente):
                continue
            if not corresponde_consultor_filtro(consultor_filtro, consultor_cliente):
                continue
            if not corresponde_termo_busca(termo, cliente, campos_busca):
                continue
            clientes_filtrados.append(dict(cliente))

        if not clientes_filtrados:
            continue

        representantes_data[nome_grupo] = {
            "nome": nome_grupo,
            "clientes": clientes_filtrados,
            "total_clientes": 0,
            "liberados": 0,
            "inadimplentes": 0,
            "sem_conceito": 0,
            "ticket_medio": 0,
            "dias_medio": 0,
            "consultores_internos": dict(dados_grupo.get("consultores_internos") or {}),
        }
        ordem_grupo = dados_grupo.get("ordem_grupo")
        if ordem_grupo is not None:
            representantes_data[nome_grupo]["ordem_grupo"] = ordem_grupo

    return consolidar_dados_grupos(
        representantes_data=representantes_data,
        chave_sem_grupo=chave_sem_grupo,
        conceitos_sem_conceito=conceitos_sem_conceito,
    )
