from datetime import datetime, timedelta
from typing import Any

from routes.clientes_ligacoes.consultor_mapping import (
    carregar_mapa_nome_para_id_usuarios_ativos,
    construir_mapa_codigo_para_id,
)
from routes.clientes_ligacoes.domain_utils import (
    _cliente_tem_representante_vinculado,
    _resolver_consultor_id_por_categoria,
)


def _montar_status_contato_mensal(ligacoes):
    agora = datetime.now()
    inicio_mes_atual = datetime(agora.year, agora.month, 1)
    if agora.month == 1:
        inicio_mes_anterior = datetime(agora.year - 1, 12, 1)
    else:
        inicio_mes_anterior = datetime(agora.year, agora.month - 1, 1)

    for lig in ligacoes:
        data_ref = getattr(lig, "data_hora", None)
        if not data_ref:
            continue
        if data_ref >= inicio_mes_atual:
            return {
                "status": "mes_atual",
                "label": "Contatado neste mes",
                "hint": "Contato realizado no mes atual. O reloginho fica verde quando o cliente especial ja foi trabalhado neste mes.",
            }
        if data_ref >= inicio_mes_anterior:
            return {
                "status": "mes_anterior",
                "label": "Contato so no mes passado",
                "hint": "O ultimo contato foi no mes passado. O reloginho fica amarelo para sinalizar que este cliente especial ainda precisa ser trabalhado novamente neste mes.",
            }
        return {
            "status": "atrasado",
            "label": "Sem contato neste mes",
            "hint": "Ainda nao houve contato no mes atual. O reloginho fica vermelho para lembrar que clientes especiais precisam ser trabalhados todo mes.",
        }

    return {
        "status": "sem_historico",
        "label": "Sem historico de contato",
        "hint": "Este cliente especial ainda nao tem ligacoes registradas. O reloginho fica cinza/vermelho para indicar pendencia no mes atual.",
    }


def classificar_listas_operacionais(
    *,
    clientes_todos: list[Any],
    current_user: Any,
    aba: str,
    codigos_representantes_vinculados: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pendentes, contatados, precisa_retornar = [], [], []
    agora = datetime.now()
    limite_min_90_150 = agora - timedelta(days=150)
    limite_max_90_150 = agora - timedelta(days=90)
    filtrar_por_categoria_consultor = current_user.tipo == "consultor"
    ajustar_consultor_supervisor_pendentes = (current_user.tipo == "supervisor" and aba == "pendentes")
    mapa_nome_para_id = {}
    mapa_codigo_para_id = {}
    ids_usuarios_ativos = set()
    if filtrar_por_categoria_consultor or ajustar_consultor_supervisor_pendentes:
        usuarios_ativos, mapa_nome_para_id = carregar_mapa_nome_para_id_usuarios_ativos()
        ids_usuarios_ativos = {u.id for u in usuarios_ativos if u and u.id}
        mapa_codigo_para_id = construir_mapa_codigo_para_id(mapa_nome_para_id)

    for c in clientes_todos:
        if current_user.tipo in ("supervisor_repr", "representante"):
            if not _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados):
                continue

        ligacoes_relevantes = (
            [l for l in c.ligacoes if l.consultor_id == current_user.id]
            if current_user.tipo in ("consultor", "televendas")
            else list(c.ligacoes)
        )
        ligs = sorted(ligacoes_relevantes, key=lambda x: x.data_hora, reverse=True)
        ultima = ligs[0] if ligs else None
        total = len(ligs)
        origem_cliente = str(getattr(c, "origem", "") or "").strip().lower()
        if current_user.tipo == "consultor" and origem_cliente not in ("manual", "importado_csv"):
            continue
        consultor_id_view = c.consultor_id
        if ajustar_consultor_supervisor_pendentes and c.cd_cliente_oracle and c.categoria_consultor:
            consultor_esperado = _resolver_consultor_id_por_categoria(
                c.categoria_consultor,
                mapa_codigo_para_id=mapa_codigo_para_id,
                mapa_nome_para_id=mapa_nome_para_id,
            )
            if consultor_esperado:
                consultor_id_view = consultor_esperado
        if ajustar_consultor_supervisor_pendentes and c.consultor_id:
            if c.consultor_id not in ids_usuarios_ativos:
                consultor_id_view = consultor_id_view if consultor_id_view != c.consultor_id else None
        status_contato_mensal = _montar_status_contato_mensal(ligs)
        dados = {
            "id": c.id,
            "nome": c.nome,
            "cnpj": c.cnpj,
            "telefone": c.telefone,
            "telefone2": c.telefone2,
            "representante_nome": (c.representante_oracle or c.representante_nome),
            "representante_oracle": c.representante_oracle or "",
            "ultima_ligacao": ultima.data_hora if ultima else None,
            "ultima_ligacao_por": None,
            "total_ligacoes": total,
            "proxima_ligacao": c.proxima_ligacao,
            "origem": getattr(c, "origem", None),
            "valor_total_365dias": c.valor_total_365dias,
            "valor_ultimo_pedido": c.valor_ultimo_pedido,
            "cd_cliente_oracle": c.cd_cliente_oracle,
            "categoria_consultor": c.categoria_consultor or "",
            "centralizadora": "",
            "consultor_id": consultor_id_view,
            "conceito": c.conceito or "",
            "municipio": c.municipio or "",
            "uf": c.uf or "",
            "contato": c.contato or "",
            "ultimo_pedido_oracle": c.ultimo_pedido_oracle,
            "situacao_ultimo_pedido": c.situacao_ultimo_pedido or "",
            "em_atendimento_ativo": bool(
                c.em_atendimento_por and c.em_atendimento_ate and c.em_atendimento_ate > datetime.now()
            ),
            "em_atendimento_por_nome": None,
            "em_atendimento_ate": (
                c.em_atendimento_ate.strftime("%d/%m/%Y %H:%M")
                if c.em_atendimento_ate and c.em_atendimento_ate > datetime.now()
                else None
            ),
            "especial_contato_mensal_status": status_contato_mensal["status"],
            "especial_contato_mensal_label": status_contato_mensal["label"],
            "especial_contato_mensal_hint": status_contato_mensal["hint"],
        }

        if (
            filtrar_por_categoria_consultor
            and c.cd_cliente_oracle
            and c.categoria_consultor
            and origem_cliente != "manual"
        ):
            consultor_esperado = _resolver_consultor_id_por_categoria(
                c.categoria_consultor,
                mapa_codigo_para_id=mapa_codigo_para_id,
                mapa_nome_para_id=mapa_nome_para_id,
            )
            if consultor_esperado and consultor_esperado != current_user.id:
                continue

        # Cliente manual sempre aparece em "Clientes Especiais".
        # Se ja houve ligacao, ele tambem participa do fluxo normal
        # (Contatados/Retornar) para manter continuidade e edicao.
        if current_user.tipo in ("consultor", "supervisor", "supervisor_repr", "representante") and origem_cliente == "manual":
            pendentes.append(dados)
            if total == 0:
                continue

        if total == 0:
            # Evita misturar campanha 90-150d na aba operacional de pendentes
            # e no badge "Clientes Especiais" do consultor.
            if (
                current_user.tipo in ("consultor", "supervisor_repr", "representante")
                and c.cd_cliente_oracle
                and c.ultimo_pedido_oracle
                and limite_min_90_150 <= c.ultimo_pedido_oracle <= limite_max_90_150
            ):
                continue
            pendentes.append(dados)
        else:
            if c.proxima_ligacao or (ultima and ultima.resultado == "retornar"):
                dados["retorno_atrasado"] = bool(c.proxima_ligacao and (agora >= c.proxima_ligacao))
                precisa_retornar.append(dados)
            else:
                contatados.append(dados)

    return pendentes, contatados, precisa_retornar
