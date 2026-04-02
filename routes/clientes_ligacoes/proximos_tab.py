from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from core.extensions import db
from core.models import Cliente, Ligacao
from routes.clientes_ligacoes.domain_utils import _cliente_tem_representante_vinculado


def preparar_contexto_proximos_inativacao(current_user, codigos_representantes_vinculados):
    agora_px = datetime.now()
    limite_min_px = agora_px - timedelta(days=180)
    limite_max_px = agora_px - timedelta(days=151)

    q_proximos = (
        Cliente.query
        .options(joinedload(Cliente.consultor))
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.between(limite_min_px, limite_max_px),
        )
    )
    if current_user.tipo in ("consultor", "televendas"):
        q_proximos = q_proximos.filter(Cliente.consultor_id == current_user.id)

    clientes_proximos_raw = q_proximos.all()

    if current_user.tipo == "supervisor_repr":
        clientes_proximos_raw = [
            c for c in clientes_proximos_raw
            if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
        ]

    ids_proximos = [c.id for c in clientes_proximos_raw]
    stats_lig_px = {}
    if ids_proximos:
        lig_agg_px = (
            db.session.query(
                Ligacao.cliente_id,
                func.count(Ligacao.id).label("total_ligacoes"),
                func.max(Ligacao.data_hora).label("ultima_ligacao"),
            )
            .filter(Ligacao.cliente_id.in_(ids_proximos))
            .group_by(Ligacao.cliente_id)
            .all()
        )
        stats_lig_px = {
            row.cliente_id: {
                "total_ligacoes": int(row.total_ligacoes or 0),
                "ultima_ligacao": row.ultima_ligacao,
            }
            for row in lig_agg_px
        }

    agrupar_por_representante_px = current_user.tipo in ("consultor", "televendas", "supervisor_repr")
    grupos_px = {}
    for c in clientes_proximos_raw:
        consultor_nome = (c.consultor.nome if c.consultor else None) or "SEM CONSULTOR"
        representante_nome = (c.representante_oracle or c.representante_nome or "").strip() or "SEM REPRESENTANTE"
        nome_grupo = representante_nome if agrupar_por_representante_px else consultor_nome

        if nome_grupo not in grupos_px:
            grupos_px[nome_grupo] = {
                "nome": nome_grupo,
                "clientes": [],
                "total_clientes": 0,
                "liberados": 0,
                "inadimplentes": 0,
                "sem_conceito": 0,
                "ticket_medio": 0,
                "dias_medio": 0,
                "consultores_internos": {},
            }

        st_lig = stats_lig_px.get(c.id, {})
        dias_sem = (agora_px - c.ultimo_pedido_oracle).days if c.ultimo_pedido_oracle else 0
        dias_para_inativar = max(0, 181 - dias_sem)
        data_prevista_inativacao = (
            c.ultimo_pedido_oracle + timedelta(days=181)
            if c.ultimo_pedido_oracle else None
        )

        dados_px = {
            "id": c.id,
            "nome": c.nome,
            "cnpj": c.cnpj,
            "telefone": c.telefone,
            "telefone2": c.telefone2,
            "representante_nome": c.representante_oracle or c.representante_nome or "",
            "representante_oracle": c.representante_oracle or "",
            "ultima_ligacao": st_lig.get("ultima_ligacao"),
            "ultima_ligacao_por": None,
            "total_ligacoes": st_lig.get("total_ligacoes", 0),
            "proxima_ligacao": c.proxima_ligacao,
            "origem": c.origem,
            "cd_cliente_oracle": c.cd_cliente_oracle,
            "categoria_consultor": c.categoria_consultor or "",
            "centralizadora": "",
            "consultor_id": c.consultor_id,
            "conceito": c.conceito or "",
            "municipio": c.municipio or "",
            "uf": c.uf or "",
            "contato": c.contato or "",
            "ultimo_pedido_oracle": c.ultimo_pedido_oracle,
            "valor_ultimo_pedido": c.valor_ultimo_pedido,
            "valor_total_365dias": c.valor_total_365dias or 0,
            "situacao_ultimo_pedido": c.situacao_ultimo_pedido or "",
            "em_atendimento_ativo": bool(c.em_atendimento_por),
            "em_atendimento_por_nome": None,
            "em_atendimento_ate": None,
            "dias_sem_pedido": dias_sem,
            "dias_para_inativar": dias_para_inativar,
            "data_prevista_inativacao": data_prevista_inativacao,
        }
        grupos_px[nome_grupo]["clientes"].append(dados_px)

    for dados in grupos_px.values():
        cls = dados["clientes"]
        dados["total_clientes"] = len(cls)
        dados["liberados"] = sum(1 for c in cls if c.get("conceito") == "LIBERADO")
        dados["inadimplentes"] = sum(1 for c in cls if c.get("conceito") == "INADIMPLENTE")
        dados["sem_conceito"] = sum(1 for c in cls if c.get("conceito") in ("", "SEM CONCEITO", None))
        vals = [c.get("valor_ultimo_pedido", 0) for c in cls if c.get("valor_ultimo_pedido")]
        dados["ticket_medio"] = sum(vals) / len(vals) if vals else 0
        dias_list = [c.get("dias_sem_pedido", 0) for c in cls if c.get("dias_sem_pedido")]
        dados["dias_medio"] = sum(dias_list) / len(dias_list) if dias_list else 0
        dados["clientes"] = sorted(cls, key=lambda x: -x.get("dias_sem_pedido", 0))

    representantes_ordenados_px = sorted(
        grupos_px.items(),
        key=lambda x: (
            -x[1]["total_clientes"],
            x[0] == ("SEM REPRESENTANTE" if agrupar_por_representante_px else "SEM CONSULTOR"),
            x[0],
        ),
    )

    total_proximos_count = sum(len(d["clientes"]) for d in grupos_px.values())
    total_lib_px = sum(d["liberados"] for d in grupos_px.values())
    total_inad_px = sum(d["inadimplentes"] for d in grupos_px.values())
    total_sc_px = sum(d["sem_conceito"] for d in grupos_px.values())
    todos_vals_px = [
        c.get("valor_ultimo_pedido")
        for _, d in grupos_px.items()
        for c in d["clientes"]
        if c.get("valor_ultimo_pedido")
    ]
    todos_dias_px = [
        c.get("dias_sem_pedido", 0)
        for _, d in grupos_px.items()
        for c in d["clientes"]
    ]
    ticket_medio_px = sum(todos_vals_px) / len(todos_vals_px) if todos_vals_px else 0
    dias_medio_px = sum(todos_dias_px) / len(todos_dias_px) if todos_dias_px else 0
    stats_proximos = {
        "liberados": total_lib_px,
        "inadimplentes": total_inad_px,
        "sem_conceito": total_sc_px,
        "ticket_medio": ticket_medio_px,
        "dias_sem_pedido": int(dias_medio_px),
        "perc_liberados": round((total_lib_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
        "perc_inadimplentes": round((total_inad_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
        "perc_sem_conceito": round((total_sc_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
    }

    return representantes_ordenados_px, total_proximos_count, stats_proximos
