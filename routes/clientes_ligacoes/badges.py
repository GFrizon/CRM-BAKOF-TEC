from datetime import datetime, timedelta

from core.models import Cliente
from routes.clientes_ligacoes.domain_utils import (
    _cliente_tem_representante_vinculado,
    _codigo_representante_de_texto,
    _normalizar_codigo_representante,
)


def _total_oracle_badge():
    """Conta clientes da janela 90-120 dias sem pedido na base local sincronizada."""
    agora = datetime.now()
    limite_min = agora - timedelta(days=120)
    limite_max = agora - timedelta(days=90)
    return (
        Cliente.query
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
        )
        .count()
    )


def _total_oracle_badge_supervisor_repr(codigos_representantes_vinculados):
    """Conta clientes 90-120d de supervisor_repr com a mesma fonte da aba Oracle."""
    if not codigos_representantes_vinculados:
        return 0

    try:
        from oracle_service import get_clientes_oracle

        clientes_oracle_raw = get_clientes_oracle() or []
        clientes_oracle_por_cd = {}
        for row in clientes_oracle_raw:
            cd = str(row.get("cd_cliente") or "").strip()
            if not cd:
                continue
            atual = clientes_oracle_por_cd.get(cd)
            if not atual:
                clientes_oracle_por_cd[cd] = row
                continue
            dt_novo = row.get("dt_pedido")
            dt_atual = atual.get("dt_pedido")
            if dt_novo and (not dt_atual or dt_novo > dt_atual):
                clientes_oracle_por_cd[cd] = row

        clientes_oracle = list(clientes_oracle_por_cd.values())
        return sum(
            1
            for row in clientes_oracle
            if _normalizar_codigo_representante(
                _codigo_representante_de_texto(str(row.get("representante") or ""))
            ) in codigos_representantes_vinculados
        )
    except Exception:
        agora = datetime.now()
        limite_min = agora - timedelta(days=120)
        limite_max = agora - timedelta(days=90)
        clientes_oracle = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
            )
            .all()
        )
        return sum(
            1
            for c in clientes_oracle
            if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
        )


def _total_inativos_badge(consultor_id=None):
    """Conta clientes inativos (181 a 730 dias sem pedido) na base local sincronizada."""
    agora = datetime.now()
    limite_max = agora - timedelta(days=181)
    limite_min = agora - timedelta(days=730)

    q = (
        Cliente.query
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
        )
    )
    if consultor_id:
        q = q.filter(Cliente.consultor_id == consultor_id)
    return q.count()


def _total_proximos_badge(consultor_id=None):
    """Conta clientes próximos de inativação (151 a 180 dias sem pedido)."""
    agora = datetime.now()
    limite_max = agora - timedelta(days=151)
    limite_min = agora - timedelta(days=180)
    q = (
        Cliente.query
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
        )
    )
    if consultor_id:
        q = q.filter(Cliente.consultor_id == consultor_id)
    return q.count()


def calcular_total_inativos_badge_com_cache(current_user, apenas_meus, cache_store, cache_ttl_seconds):
    total_inativos_badge = 0
    if current_user.tipo in ("televendas", "supervisor"):
        cache = cache_store.get(current_user.id)
        if cache and cache.get("ts"):
            idade = (datetime.now() - cache["ts"]).total_seconds()
            if idade <= cache_ttl_seconds:
                total_inativos_badge = int(cache.get("count") or 0)
        if total_inativos_badge == 0:
            # Televendas vê todos os inativos na aba (sem filtro por consultor_id),
            # então o badge deve refletir o total global, não apenas os do usuário.
            consultor_inativos = current_user.id if (apenas_meus and current_user.tipo == "consultor") else None
            total_inativos_badge = _total_inativos_badge(consultor_inativos)
            cache_store[current_user.id] = {
                "count": int(total_inativos_badge),
                "ts": datetime.now(),
            }
    return total_inativos_badge
