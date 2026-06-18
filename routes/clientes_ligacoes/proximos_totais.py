from datetime import datetime

from core.extensions import db
from core.models import Cliente, Ligacao
from routes.clientes_ligacoes.domain_utils import _cliente_tem_representante_vinculado
from routes.clientes_ligacoes.listagem_base_filters import aplicar_filtro_carteira_especial_consultor
from sqlalchemy.orm import load_only


_REPRESENTANTE_TOTAIS_CACHE = {}
_REPRESENTANTE_TOTAIS_CACHE_TTL_SECONDS = 300


def _cache_key(codigos_representantes_vinculados):
    codigos = tuple(sorted(str(c or "").strip() for c in (codigos_representantes_vinculados or []) if str(c or "").strip()))
    return codigos


def _cache_get(codigos_representantes_vinculados):
    chave = _cache_key(codigos_representantes_vinculados)
    if not chave:
        return None
    item = _REPRESENTANTE_TOTAIS_CACHE.get(chave)
    if not item:
        return None
    idade = (datetime.now() - item["ts"]).total_seconds()
    if idade > _REPRESENTANTE_TOTAIS_CACHE_TTL_SECONDS:
        _REPRESENTANTE_TOTAIS_CACHE.pop(chave, None)
        return None
    return int(item.get("pendentes") or 0), int(item.get("retornar") or 0)


def _cache_set(codigos_representantes_vinculados, pendentes, retornar):
    chave = _cache_key(codigos_representantes_vinculados)
    if not chave:
        return
    _REPRESENTANTE_TOTAIS_CACHE[chave] = {
        "pendentes": int(pendentes or 0),
        "retornar": int(retornar or 0),
        "ts": datetime.now(),
    }


def calcular_totais_abas_proximos(current_user, codigos_representantes_vinculados):
    apenas_meus_px = current_user.tipo in ("consultor", "televendas")
    base_q_px = Cliente.query.filter_by(ativo=True)
    base_q_px = aplicar_filtro_carteira_especial_consultor(base_q_px, current_user)
    if apenas_meus_px:
        base_q_px = base_q_px.filter(Cliente.consultor_id == current_user.id)

    if current_user.tipo in ("supervisor_repr", "representante"):
        cache = _cache_get(codigos_representantes_vinculados)
        if cache is not None:
            return cache
        base_clientes_px = [
            c for c in (
                base_q_px
                .options(
                    load_only(
                        Cliente.id,
                        Cliente.representante_oracle,
                        Cliente.representante_nome,
                        Cliente.proxima_ligacao,
                    )
                )
                .all()
            )
            if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
        ]
        ids_px = [c.id for c in base_clientes_px if c.id]
        ligados_ids_px = set()
        if ids_px:
            rows_lig_px = (
                db.session.query(Ligacao.cliente_id)
                .filter(Ligacao.cliente_id.in_(ids_px))
                .distinct()
                .all()
            )
            ligados_ids_px = {row.cliente_id for row in rows_lig_px if row.cliente_id}

        total_pendentes_px = sum(1 for c in base_clientes_px if c.id not in ligados_ids_px)
        total_retornar_px = sum(1 for c in base_clientes_px if c.proxima_ligacao is not None)
        _cache_set(
            codigos_representantes_vinculados,
            total_pendentes_px,
            total_retornar_px,
        )
        return total_pendentes_px, total_retornar_px

    clig_px = (
        db.session.query(Ligacao.cliente_id)
        .filter(Ligacao.consultor_id == current_user.id)
        .distinct()
    ) if apenas_meus_px else db.session.query(Ligacao.cliente_id).distinct()
    total_pendentes_px = base_q_px.filter(Cliente.id.notin_(clig_px)).count()
    total_retornar_px = base_q_px.filter(Cliente.proxima_ligacao.isnot(None)).count()
    return total_pendentes_px, total_retornar_px
