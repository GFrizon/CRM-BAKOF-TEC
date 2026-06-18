"""
Pré-aquecimento dos snapshots Oracle na inicialização do app.

Executa em background (thread daemon) para não atrasar o startup.
Aquece em paralelo: ativos, inativos e construtoras.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


def _aquece_ativos(app):
    try:
        from oracle_service import get_clientes_ativos_oracle
        from services.ativos_snapshot_service import (
            carregar_snapshot_ativos_oracle,
            salvar_snapshot_ativos_oracle,
        )
        with app.app_context():
            if carregar_snapshot_ativos_oracle():
                logger.info("[warmup] ativos: snapshot do dia ja existe, pulando")
                return
            t0 = datetime.now()
            rows = get_clientes_ativos_oracle() or []
            salvar_snapshot_ativos_oracle(rows)
            elapsed = (datetime.now() - t0).total_seconds()
            logger.info(f"[warmup] ativos: {len(rows)} registros em {elapsed:.1f}s")
    except Exception as e:
        logger.warning(f"[warmup] ativos falhou: {e}")


def _aquece_inativos(app):
    try:
        from oracle_service import get_clientes_inativos_oracle
        from services.inativos_snapshot_service import (
            carregar_snapshot_inativos_oracle,
            salvar_snapshot_inativos_oracle,
            snapshot_inativos_cobre_janela,
        )
        with app.app_context():
            snapshot = carregar_snapshot_inativos_oracle()
            if snapshot and snapshot_inativos_cobre_janela(snapshot):
                logger.info("[warmup] inativos: snapshot do dia ja existe, pulando")
                return
            t0 = datetime.now()
            rows = get_clientes_inativos_oracle() or []
            salvar_snapshot_inativos_oracle(rows)
            elapsed = (datetime.now() - t0).total_seconds()
            logger.info(f"[warmup] inativos: {len(rows)} registros em {elapsed:.1f}s")
    except Exception as e:
        logger.warning(f"[warmup] inativos falhou: {e}")


def _aquece_construtoras(app):
    try:
        from oracle_service import get_clientes_construtoras_oracle
        from services.construtoras_snapshot_service import (
            carregar_snapshot_construtoras_oracle,
            salvar_snapshot_construtoras_oracle,
        )
        with app.app_context():
            if carregar_snapshot_construtoras_oracle():
                logger.info("[warmup] construtoras: snapshot do dia ja existe, pulando")
                return
            t0 = datetime.now()
            rows = get_clientes_construtoras_oracle() or []
            salvar_snapshot_construtoras_oracle(rows)
            elapsed = (datetime.now() - t0).total_seconds()
            logger.info(f"[warmup] construtoras: {len(rows)} registros em {elapsed:.1f}s")
    except Exception as e:
        logger.warning(f"[warmup] construtoras falhou: {e}")


def _aquece_oracle_especiais(app):
    try:
        from oracle_service import get_clientes_oracle
        from services.oracle_snapshot_service import (
            carregar_snapshot_oracle_90_150,
            salvar_snapshot_oracle_90_150,
        )
        with app.app_context():
            if carregar_snapshot_oracle_90_150():
                logger.info("[warmup] especiais (90-150d): snapshot do dia ja existe, pulando")
                return
            t0 = datetime.now()
            rows = get_clientes_oracle() or []
            salvar_snapshot_oracle_90_150(rows)
            elapsed = (datetime.now() - t0).total_seconds()
            logger.info(f"[warmup] especiais (90-150d): {len(rows)} registros em {elapsed:.1f}s")
    except Exception as e:
        logger.warning(f"[warmup] especiais (90-150d) falhou: {e}")


def _aquece_proximos_inativacao(app):
    try:
        from oracle_service import get_clientes_proximos_inativacao_oracle
        from services.proximos_inativacao_snapshot_service import (
            carregar_snapshot_proximos_inativacao,
            salvar_snapshot_proximos_inativacao,
        )
        with app.app_context():
            if carregar_snapshot_proximos_inativacao():
                logger.info("[warmup] proximos_inativacao: snapshot do dia ja existe, pulando")
                return
            t0 = datetime.now()
            rows = get_clientes_proximos_inativacao_oracle() or []
            salvar_snapshot_proximos_inativacao(rows)
            elapsed = (datetime.now() - t0).total_seconds()
            logger.info(f"[warmup] proximos_inativacao: {len(rows)} registros em {elapsed:.1f}s")
    except Exception as e:
        logger.warning(f"[warmup] proximos_inativacao falhou: {e}")


def _coletar_todos_codigos_snapshots():
    """Coleta todos os codigos de clientes dos snapshots disponiveis. Deve ser chamado dentro de app_context."""
    from services.ativos_snapshot_service import (
        carregar_snapshot_ativos_oracle,
        montar_mapa_snapshot_ativos,
    )
    from services.oracle_snapshot_service import (
        carregar_snapshot_oracle_90_150,
        rows_snapshot_oracle_90_150,
    )
    from services.inativos_snapshot_service import carregar_snapshot_inativos_oracle
    from services.construtoras_snapshot_service import (
        carregar_snapshot_construtoras_oracle,
        rows_snapshot_construtoras,
    )
    from services.proximos_inativacao_snapshot_service import carregar_snapshot_proximos_inativacao
    codigos = set()
    snap_ativos = carregar_snapshot_ativos_oracle()
    if snap_ativos:
        cods, _ = montar_mapa_snapshot_ativos(snap_ativos)
        codigos.update(cods)
    snap_90_150 = carregar_snapshot_oracle_90_150()
    if snap_90_150:
        for item in rows_snapshot_oracle_90_150(snap_90_150):
            cd = str((item or {}).get("cd_cliente") or "").strip()
            if cd:
                codigos.add(cd)
    snap_inativos = carregar_snapshot_inativos_oracle()
    for item in ((snap_inativos or {}).get("itens") or []):
        cd = str((item or {}).get("cd_cliente") or "").strip()
        if cd:
            codigos.add(cd)
    snap_construtoras = carregar_snapshot_construtoras_oracle()
    if snap_construtoras:
        for item in rows_snapshot_construtoras(snap_construtoras):
            cd = str((item or {}).get("cd_cliente") or "").strip()
            if cd:
                codigos.add(cd)
    snap_proximos = carregar_snapshot_proximos_inativacao()
    for item in ((snap_proximos or {}).get("itens") or []):
        cd = str((item or {}).get("cd_cliente") or "").strip()
        if cd:
            codigos.add(cd)
    return codigos


def _aquece_pagamento_medio(app):
    try:
        from services.representante_metricas_cache_service import carregar_pagamento_medio_representante
        with app.app_context():
            codigos = list(_coletar_todos_codigos_snapshots())
            if not codigos:
                logger.info("[warmup] pagamento_medio: sem codigos, pulando")
                return
            t0 = datetime.now()
            carregar_pagamento_medio_representante(codigos)
            elapsed = (datetime.now() - t0).total_seconds()
            logger.info(f"[warmup] pagamento_medio: {len(codigos)} codigos em {elapsed:.1f}s")
    except Exception as e:
        logger.warning(f"[warmup] pagamento_medio falhou: {e}")


def _aquece_meses_compra(app):
    try:
        from services.representante_metricas_cache_service import carregar_meses_compra_representante
        with app.app_context():
            codigos = list(_coletar_todos_codigos_snapshots())
            if not codigos:
                logger.info("[warmup] meses_compra: sem codigos, pulando")
                return
            t0 = datetime.now()
            for periodo in ("ano_atual", "ultimos_365_dias", "ultimos_3_anos"):
                carregar_meses_compra_representante(codigos, periodo=periodo)
            elapsed = (datetime.now() - t0).total_seconds()
            logger.info(f"[warmup] meses_compra: {len(codigos)} codigos em {elapsed:.1f}s")
    except Exception as e:
        logger.warning(f"[warmup] meses_compra falhou: {e}")


def aquecer_metricas_dependentes_oracle(app):
    """Reconstroi caches derivados depois que os snapshots forem atualizados."""
    _aquece_pagamento_medio(app)
    _aquece_meses_compra(app)


def _aquece_snapshots_e_dependentes(app):
    """Aquece snapshots em paralelo e, depois que todos terminam, aquece os caches dependentes."""
    snapshot_threads = [
        ("warmup-ativos", _aquece_ativos),
        ("warmup-inativos", _aquece_inativos),
        ("warmup-construtoras", _aquece_construtoras),
        ("warmup-especiais", _aquece_oracle_especiais),
        ("warmup-proximos-inativacao", _aquece_proximos_inativacao),
    ]
    workers = []
    for name, fn in snapshot_threads:
        t = threading.Thread(target=fn, args=(app,), name=name, daemon=True)
        t.start()
        workers.append(t)
        logger.info(f"[warmup] thread '{name}' iniciada")

    for t in workers:
        t.join()

    dep_threads = [
        ("warmup-pagamento_medio", _aquece_pagamento_medio),
        ("warmup-meses_compra", _aquece_meses_compra),
    ]
    for name, fn in dep_threads:
        t = threading.Thread(target=fn, args=(app,), name=name, daemon=True)
        t.start()
        logger.info(f"[warmup] thread '{name}' iniciada")


def iniciar_warmup_oracle(app):
    """
    Dispara o pré-aquecimento dos snapshots Oracle em threads daemon paralelas.
    Retorna imediatamente sem bloquear o startup.
    """
    # Evita rodar no processo pai do reloader do Werkzeug
    import os
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    t = threading.Thread(
        target=_aquece_snapshots_e_dependentes,
        args=(app,),
        name="warmup-coordinator",
        daemon=True,
    )
    t.start()
    logger.info("[warmup] coordinator iniciado")
