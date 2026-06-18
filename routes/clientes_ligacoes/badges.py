import logging
from datetime import datetime, timedelta

from core.models import Cliente
from routes.clientes_ligacoes.ativos_tab import carregar_clientes_ativos_oracle_deduplicados
from routes.clientes_ligacoes.consultor_mapping import (
    carregar_mapa_nome_para_id_usuarios_ativos,
    construir_mapa_codigo_para_id,
)
from routes.clientes_ligacoes.construtoras_tab import carregar_clientes_construtoras_deduplicados
from routes.clientes_ligacoes.domain_utils import (
    _cliente_tem_representante_vinculado,
    _codigo_representante_de_texto,
    _normalizar_codigo_representante,
)
from routes.clientes_ligacoes.inativos_cnpj_raiz_filter import (
    filtrar_clientes_inativos_por_cnpj_raiz_oculto,
)
from routes.clientes_ligacoes.inativos_tab import carregar_clientes_inativos_enriquecidos
from routes.clientes_ligacoes.listagem_permissions import (
    consultor_categoria_permitido_para_usuario,
)
from routes.clientes_ligacoes.oracle_tab import carregar_clientes_oracle_deduplicados
from routes.clientes_ligacoes.representante_snapshot_readers import (
    carregar_clientes_inativos_snapshot_representante,
)
from services.ativos_snapshot_service import carregar_snapshot_ativos_oracle
from services.construtoras_snapshot_service import (
    carregar_snapshot_construtoras_oracle,
    rows_snapshot_construtoras,
)
from services.oracle_snapshot_service import (
    carregar_snapshot_oracle_90_150,
    rows_snapshot_oracle_90_150,
)

logger = logging.getLogger(__name__)
_REPRESENTANTE_BADGE_CACHE = {}
_REPRESENTANTE_BADGE_CACHE_TTL = timedelta(minutes=5)


def limpar_cache_badges_representante():
    _REPRESENTANTE_BADGE_CACHE.clear()


def _cache_representante_get(tipo: str, codigos_representantes_vinculados):
    chave_codigos = tuple(
        sorted(str(c or "") for c in (codigos_representantes_vinculados or []) if str(c or ""))
    )
    if not chave_codigos:
        return None
    cache = _REPRESENTANTE_BADGE_CACHE.get((tipo, chave_codigos))
    if not cache or not cache.get("ts"):
        return None
    if (datetime.now() - cache["ts"]) > _REPRESENTANTE_BADGE_CACHE_TTL:
        return None
    return int(cache.get("count") or 0)


def _cache_representante_set(tipo: str, codigos_representantes_vinculados, valor: int):
    chave_codigos = tuple(
        sorted(str(c or "") for c in (codigos_representantes_vinculados or []) if str(c or ""))
    )
    if not chave_codigos:
        return
    _REPRESENTANTE_BADGE_CACHE[(tipo, chave_codigos)] = {
        "count": int(valor or 0),
        "ts": datetime.now(),
    }


def _count_rows_by_representante(rows, codigos_representantes_vinculados):
    if not codigos_representantes_vinculados:
        return 0
    return sum(
        1
        for row in (rows or [])
        if _normalizar_codigo_representante(
            _codigo_representante_de_texto(str((row or {}).get("representante") or ""))
        ) in codigos_representantes_vinculados
    )


def _total_oracle_badge(consultor_id=None):
    agora = datetime.now()
    limite_min = agora - timedelta(days=150)
    limite_max = agora - timedelta(days=90)
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


def _total_oracle_badge_supervisor_repr(codigos_representantes_vinculados):
    if not codigos_representantes_vinculados:
        return 0

    cache_count = _cache_representante_get("oracle_90_150", codigos_representantes_vinculados)
    if cache_count is not None:
        return cache_count

    try:
        snapshot = carregar_snapshot_oracle_90_150()
        if snapshot and isinstance(snapshot.get("itens"), list):
            total = _count_rows_by_representante(
                rows_snapshot_oracle_90_150(snapshot),
                codigos_representantes_vinculados,
            )
            _cache_representante_set("oracle_90_150", codigos_representantes_vinculados, total)
            return total
    except Exception:
        pass

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
        total = _count_rows_by_representante(clientes_oracle, codigos_representantes_vinculados)
        _cache_representante_set("oracle_90_150", codigos_representantes_vinculados, total)
        return total
    except Exception:
        agora = datetime.now()
        limite_min = agora - timedelta(days=150)
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
        total = sum(
            1
            for c in clientes_oracle
            if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
        )
        _cache_representante_set("oracle_90_150", codigos_representantes_vinculados, total)
        return total


def _total_oracle_badge_consultor_lista_oracle(consultor_id: int) -> int:
    if not consultor_id:
        return 0

    clientes_oracle = carregar_clientes_oracle_deduplicados(logger, periodo_oracle=None)
    if not clientes_oracle:
        return 0

    codigos_oracle = {
        str(c.get("cd_cliente") or "").strip()
        for c in clientes_oracle
        if c.get("cd_cliente")
    }
    if not codigos_oracle:
        return 0

    clientes_consultor = (
        Cliente.query
        .filter(
            Cliente.ativo == True,
            Cliente.consultor_id == consultor_id,
            Cliente.cd_cliente_oracle.in_(list(codigos_oracle)),
        )
        .all()
    )
    codigos_consultor = {
        str(c.cd_cliente_oracle).strip()
        for c in clientes_consultor
        if c.cd_cliente_oracle
    }
    if not codigos_consultor:
        return 0

    _, mapa_nome_para_id_oracle = carregar_mapa_nome_para_id_usuarios_ativos()
    mapa_codigo_para_id_oracle = construir_mapa_codigo_para_id(mapa_nome_para_id_oracle)

    total = 0
    for row in clientes_oracle:
        cd_cliente = str(row.get("cd_cliente") or "").strip()
        if not cd_cliente or cd_cliente not in codigos_consultor:
            continue
        consultor_cliente = str(row.get("consultor") or "").strip()
        if not consultor_categoria_permitido_para_usuario(
            tipo_usuario="consultor",
            consultor_cliente=consultor_cliente,
            current_user_id=consultor_id,
            mapa_codigo_para_id=mapa_codigo_para_id_oracle,
            mapa_nome_para_id=mapa_nome_para_id_oracle,
        ):
            continue
        total += 1
    return total


def _total_oracle_badge_supervisor_lista_oracle() -> int:
    clientes_oracle = carregar_clientes_oracle_deduplicados(logger, periodo_oracle=None)
    return len(clientes_oracle or [])


def _total_inativos_badge(consultor_id=None):
    if not consultor_id:
        try:
            clientes_oracle_inativos = carregar_clientes_inativos_enriquecidos(logger)
            clientes_oracle_inativos = filtrar_clientes_inativos_por_cnpj_raiz_oculto(
                clientes_oracle_inativos
            )
            return len(clientes_oracle_inativos or [])
        except Exception:
            pass

    agora = datetime.now()
    limite_max = agora - timedelta(days=181)
    limite_min = agora - timedelta(days=1095)
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


def _total_ativos_badge(consultor_id=None):
    if not consultor_id:
        try:
            from services.ativos_snapshot_service import carregar_snapshot_ativos_oracle

            snapshot = carregar_snapshot_ativos_oracle()
            if snapshot and snapshot.get("total"):
                return int(snapshot["total"])
        except Exception:
            pass

    agora = datetime.now()
    limite_min = agora - timedelta(days=180)
    q = (
        Cliente.query
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle >= limite_min,
            Cliente.ultimo_pedido_oracle <= agora,
        )
    )
    if consultor_id:
        q = q.filter(Cliente.consultor_id == consultor_id)
    return q.count()


def _total_ativos_badge_representante(codigos_representantes_vinculados):
    if not codigos_representantes_vinculados:
        return 0

    cache_count = _cache_representante_get("ativos", codigos_representantes_vinculados)
    if cache_count is not None:
        return cache_count

    try:
        snapshot = carregar_snapshot_ativos_oracle()
        if snapshot and isinstance(snapshot.get("itens"), list):
            total = _count_rows_by_representante(
                snapshot.get("itens") or [],
                codigos_representantes_vinculados,
            )
            _cache_representante_set("ativos", codigos_representantes_vinculados, total)
            return total
    except Exception:
        pass

    try:
        clientes_oracle = carregar_clientes_ativos_oracle_deduplicados(logger) or []
        total = _count_rows_by_representante(clientes_oracle, codigos_representantes_vinculados)
        _cache_representante_set("ativos", codigos_representantes_vinculados, total)
        return total
    except Exception:
        agora = datetime.now()
        limite_min = agora - timedelta(days=180)
        clientes_ativos = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle >= limite_min,
                Cliente.ultimo_pedido_oracle <= agora,
            )
            .all()
        )
        total = sum(
            1
            for c in clientes_ativos
            if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
        )
        _cache_representante_set("ativos", codigos_representantes_vinculados, total)
        return total


def _total_construtoras_badge():
    try:
        return len(carregar_clientes_construtoras_deduplicados(logger) or [])
    except Exception:
        return 0


def _total_construtoras_badge_representante(codigos_representantes_vinculados):
    if not codigos_representantes_vinculados:
        return 0

    cache_count = _cache_representante_get("construtoras", codigos_representantes_vinculados)
    if cache_count is not None:
        return cache_count

    try:
        snapshot = carregar_snapshot_construtoras_oracle()
        if snapshot and isinstance(snapshot.get("itens"), list):
            total = _count_rows_by_representante(
                rows_snapshot_construtoras(snapshot),
                codigos_representantes_vinculados,
            )
            _cache_representante_set("construtoras", codigos_representantes_vinculados, total)
            return total
    except Exception:
        pass

    try:
        clientes_oracle = carregar_clientes_construtoras_deduplicados(logger) or []
        total = _count_rows_by_representante(clientes_oracle, codigos_representantes_vinculados)
        _cache_representante_set("construtoras", codigos_representantes_vinculados, total)
        return total
    except Exception:
        return 0


def calcular_total_inativos_badge_com_cache(current_user, apenas_meus, cache_store, cache_ttl_seconds):
    total_inativos_badge = 0
    if current_user.tipo in ("televendas", "supervisor", "consultor", "representante"):
        cache = cache_store.get(current_user.id)
        if cache and cache.get("ts"):
            idade = (datetime.now() - cache["ts"]).total_seconds()
            if idade <= cache_ttl_seconds:
                total_inativos_badge = int(cache.get("count") or 0)
        if total_inativos_badge == 0:
            if current_user.tipo == "representante":
                codigo_representante = _normalizar_codigo_representante(
                    getattr(current_user, "codigo_representante", "")
                )
                if codigo_representante:
                    try:
                        clientes_oracle_inativos = carregar_clientes_inativos_snapshot_representante()
                        clientes_oracle_inativos = filtrar_clientes_inativos_por_cnpj_raiz_oculto(
                            clientes_oracle_inativos
                        )
                        total_inativos_badge = sum(
                            1
                            for row in (clientes_oracle_inativos or [])
                            if _normalizar_codigo_representante(
                                _codigo_representante_de_texto(str(row.get("representante") or ""))
                            ) == codigo_representante
                        )
                    except Exception:
                        clientes_inativos = (
                            Cliente.query
                            .filter(
                                Cliente.ativo == True,
                                Cliente.cd_cliente_oracle.isnot(None),
                                Cliente.ultimo_pedido_oracle.isnot(None),
                                Cliente.ultimo_pedido_oracle.between(
                                    datetime.now() - timedelta(days=1095),
                                    datetime.now() - timedelta(days=181),
                                ),
                            )
                            .all()
                        )
                        total_inativos_badge = sum(
                            1
                            for c in clientes_inativos
                            if _cliente_tem_representante_vinculado(c, [codigo_representante])
                        )
                else:
                    total_inativos_badge = 0
            else:
                total_inativos_badge = _total_inativos_badge(None)
            cache_store[current_user.id] = {
                "count": int(total_inativos_badge),
                "ts": datetime.now(),
            }
    return total_inativos_badge
