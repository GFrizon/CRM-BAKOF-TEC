from __future__ import annotations

from typing import Any, Mapping


def montar_status_contato_mensal_por_data(ultima_ligacao: Any) -> dict[str, str]:
    from datetime import datetime

    agora = datetime.now()
    inicio_mes_atual = datetime(agora.year, agora.month, 1)
    inicio_mes_anterior = (
        datetime(agora.year - 1, 12, 1)
        if agora.month == 1
        else datetime(agora.year, agora.month - 1, 1)
    )

    if not ultima_ligacao:
        return {
            "status": "sem_historico",
            "label": "Sem ligacao nos ultimos 2 meses",
        }
    if ultima_ligacao >= inicio_mes_atual:
        return {
            "status": "mes_atual",
            "label": "Ligado neste mes",
        }
    if ultima_ligacao >= inicio_mes_anterior:
        return {
            "status": "mes_anterior",
            "label": "Ligado no mes passado",
        }
    return {
        "status": "atrasado",
        "label": "Sem ligacao nos ultimos 2 meses",
    }


def montar_payload_cliente_oracle(
    *,
    cliente_oracle: Mapping[str, Any],
    cliente_local: Any,
    stats_lig: Mapping[str, Any],
    lock_info: Mapping[str, Any],
    conceito: str,
    origem_padrao: str,
) -> dict[str, Any]:
    status_contato_mensal = montar_status_contato_mensal_por_data(
        stats_lig.get("ultima_ligacao")
    )
    return {
        "id": cliente_local.id if cliente_local else None,
        "nome": cliente_oracle.get("cliente", ""),
        "cnpj": cliente_oracle.get("cnpj", ""),
        "telefone": (
            cliente_local.telefone
            if cliente_local and cliente_local.telefone
            else (cliente_oracle.get("telefone1") or cliente_oracle.get("telefone2"))
        ),
        "telefone2": (cliente_local.telefone2 if cliente_local else cliente_oracle.get("telefone2")),
        "representante_nome": cliente_oracle.get("representante", "SEM REPRESENTANTE"),
        "ultima_ligacao": stats_lig.get("ultima_ligacao"),
        "ultima_ligacao_por": stats_lig.get("ultima_ligacao_por"),
        "total_ligacoes": stats_lig.get("total_ligacoes", 0),
        "especial_contato_mensal_status": status_contato_mensal["status"],
        "especial_contato_mensal_label": status_contato_mensal["label"],
        "proxima_ligacao": (cliente_local.proxima_ligacao if cliente_local else None),
        "origem": (getattr(cliente_local, "origem", None) if cliente_local else origem_padrao),
        "cd_cliente_oracle": cliente_oracle.get("cd_cliente"),
        "categoria_consultor": cliente_oracle.get("consultor", ""),
        "centralizadora": (
            f"{cliente_oracle.get('cd_centralizado')} - {cliente_oracle.get('nome_centralizadora')}"
            if cliente_oracle.get("cd_centralizado") and cliente_oracle.get("nome_centralizadora")
            else (str(cliente_oracle.get("cd_centralizado") or "").strip() or "")
        ),
        "consultor_id": (cliente_local.consultor_id if cliente_local else None),
        "conceito": conceito,
        "municipio": cliente_oracle.get("municipio", ""),
        "uf": cliente_oracle.get("uf", ""),
        "contato": cliente_oracle.get("contato", ""),
        "ultimo_pedido_oracle": cliente_oracle.get("dt_pedido"),
        "valor_ultimo_pedido": cliente_oracle.get("total_pedido"),
        "valor_total_365dias": (cliente_local.valor_total_365dias if cliente_local else 0),
        "qtd_pedidos_365d": int(cliente_oracle.get("qtd_pedidos_365d") or 0),
        "meses_com_compra": int(cliente_oracle.get("meses_com_compra") or 0),
        "meses_total_periodo": int(cliente_oracle.get("meses_total_periodo") or 0),
        "situacao_ultimo_pedido": cliente_oracle.get("situacao", ""),
        "representante_oracle": cliente_oracle.get("representante", "SEM REPRESENTANTE"),
        "em_atendimento_ativo": bool(lock_info.get("ativo")),
        "em_atendimento_por_nome": lock_info.get("por_nome"),
        "em_atendimento_ate": lock_info.get("ate"),
        "pagamento_medio_dias": cliente_oracle.get("pagamento_medio_dias"),
    }
