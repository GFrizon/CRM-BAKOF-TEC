from datetime import datetime

from core.extensions import db
from core.models import Usuario


def montar_representantes_agrupados(clientes, tipo_usuario, aba):
    agora_grp = datetime.now()
    agrupar_por_consultor = (tipo_usuario == "supervisor" and aba == "pendentes")
    mapa_consultor_nome = {}
    if agrupar_por_consultor:
        mapa_consultor_nome = {
            int(uid): (nome or "").strip()
            for uid, nome in (
                db.session.query(Usuario.id, Usuario.nome)
                .filter(Usuario.ativo == True)
                .all()
            )
        }

    grupo_sem_nome = "SEM CONSULTOR" if agrupar_por_consultor else "SEM REPRESENTANTE"
    representantes_data_grp = {}
    for item in clientes:
        if agrupar_por_consultor:
            consultor_id_item = item.get("consultor_id")
            rep_nome = (
                mapa_consultor_nome.get(int(consultor_id_item))
                if consultor_id_item else None
            ) or grupo_sem_nome
        else:
            rep_nome = (
                str(item.get("representante_oracle") or item.get("representante_nome") or "").strip()
                or grupo_sem_nome
            )
        if rep_nome not in representantes_data_grp:
            representantes_data_grp[rep_nome] = {
                "nome": rep_nome,
                "clientes": [],
                "total_clientes": 0,
                "liberados": 0,
                "inadimplentes": 0,
                "sem_conceito": 0,
                "ticket_medio": 0,
                "dias_medio": 0,
                "consultores_internos": {},
            }
        representantes_data_grp[rep_nome]["clientes"].append(item)

    for dados_rep in representantes_data_grp.values():
        cls_r = dados_rep["clientes"]
        dados_rep["total_clientes"] = len(cls_r)
        dados_rep["liberados"] = sum(1 for c in cls_r if c.get("conceito") == "LIBERADO")
        dados_rep["inadimplentes"] = sum(1 for c in cls_r if c.get("conceito") == "INADIMPLENTE")
        dados_rep["sem_conceito"] = sum(1 for c in cls_r if c.get("conceito") in ("", "SEM CONCEITO", None))
        vals_r = [c.get("valor_ultimo_pedido", 0) for c in cls_r if c.get("valor_ultimo_pedido")]
        dados_rep["ticket_medio"] = sum(vals_r) / len(vals_r) if vals_r else 0
        dias_r = [
            (agora_grp - c["ultimo_pedido_oracle"]).days
            for c in cls_r if c.get("ultimo_pedido_oracle")
        ]
        dados_rep["dias_medio"] = sum(dias_r) / len(dias_r) if dias_r else 0

    representantes_ordenados_grp = sorted(
        representantes_data_grp.items(),
        key=lambda x: (-x[1]["total_clientes"], x[0] == grupo_sem_nome, x[0]),
    )
    return representantes_ordenados_grp
