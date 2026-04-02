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


def classificar_listas_operacionais(
    *,
    clientes_todos: list[Any],
    current_user: Any,
    aba: str,
    codigos_representantes_vinculados: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pendentes, contatados, precisa_retornar = [], [], []
    agora = datetime.now()
    limite_min_90_120 = agora - timedelta(days=120)
    limite_max_90_120 = agora - timedelta(days=90)
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
        if current_user.tipo == "supervisor_repr":
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
            "em_atendimento_ativo": bool(c.em_atendimento_por),
            "em_atendimento_por_nome": None,
            "em_atendimento_ate": None,
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

        # Regra de negocio: para consultor, cliente manual pertence a
        # "Clientes Especiais" (antiga aba Pendentes), mesmo com historico.
        if current_user.tipo in ("consultor", "supervisor_repr") and origem_cliente == "manual":
            pendentes.append(dados)
            continue

        if total == 0:
            # Evita misturar campanha 90-120d na aba operacional de pendentes
            # e no badge "Clientes Especiais" do consultor.
            if (
                current_user.tipo in ("consultor", "supervisor_repr")
                and c.cd_cliente_oracle
                and c.ultimo_pedido_oracle
                and limite_min_90_120 <= c.ultimo_pedido_oracle <= limite_max_90_120
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
