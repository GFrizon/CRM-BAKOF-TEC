from sqlalchemy import or_

from core.extensions import db
from core.models import Cliente, Ligacao, Usuario


def aplicar_filtro_base_clientes(query, current_user, apenas_meus=False, dashboard_tipo=None):
    """Aplica filtro-base de clientes conforme contexto do usuario/dashboard.

    Mantem as mesmas regras ja usadas no fluxo operacional e badges.
    """
    if current_user.tipo == "supervisor" and dashboard_tipo in ("consultor", "televendas"):
        operadores_ids_query = (
            db.session.query(Usuario.id)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
        )
        query = query.filter(Cliente.consultor_id.in_(operadores_ids_query))

    if current_user.tipo == "televendas":
        clientes_ligados_por_tv = (
            db.session.query(Ligacao.cliente_id)
            .filter(Ligacao.consultor_id == current_user.id)
            .distinct()
        )
        query = query.filter(
            or_(
                Cliente.consultor_id == current_user.id,
                Cliente.id.in_(clientes_ligados_por_tv),
            )
        )
    elif apenas_meus:
        query = query.filter(Cliente.consultor_id == current_user.id)

    return query


def aplicar_filtro_carteira_especial_consultor(query, current_user):
    """Para consultor, carteira operacional 'Clientes Especiais' = manual + importado_csv."""
    if current_user.tipo == "consultor":
        query = query.filter(Cliente.origem.in_(("manual", "importado_csv")))
    return query
