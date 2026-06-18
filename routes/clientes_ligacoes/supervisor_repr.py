from datetime import datetime, timedelta

from core.models import Cliente, SupervisorRepresentanteVinculo
from routes.clientes_ligacoes.domain_utils import (
    _cliente_tem_representante_vinculado,
    _normalizar_codigo_representante,
)

_PROXIMOS_CACHE = {}
_PROXIMOS_CACHE_TTL = timedelta(minutes=5)


def obter_codigos_representantes_vinculados(supervisor_id: int):
    vinculos = SupervisorRepresentanteVinculo.query.filter_by(
        supervisor_id=supervisor_id,
        ativo=True,
    ).all()
    return [
        _normalizar_codigo_representante(v.codigo_representante)
        for v in vinculos
        if _normalizar_codigo_representante(v.codigo_representante)
    ]


def contar_proximos_inativacao_supervisor_repr(codigos_representantes_vinculados):
    chave_codigos = tuple(
        sorted(str(c or "") for c in (codigos_representantes_vinculados or []) if str(c or ""))
    )
    if not chave_codigos:
        return 0

    cache = _PROXIMOS_CACHE.get(chave_codigos)
    if cache and cache.get("ts") and (datetime.now() - cache["ts"]) <= _PROXIMOS_CACHE_TTL:
        return int(cache.get("count") or 0)

    agora = datetime.now()
    limite_max = agora - timedelta(days=151)
    limite_min = agora - timedelta(days=180)
    clientes_proximos = (
        Cliente.query
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
        )
        .all()
    )
    total = sum(
        1
        for c in clientes_proximos
        if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
    )
    _PROXIMOS_CACHE[chave_codigos] = {
        "count": int(total or 0),
        "ts": datetime.now(),
    }
    return total
