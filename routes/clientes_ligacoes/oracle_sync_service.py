import logging
import time

from sqlalchemy import or_

from core.extensions import db
from core.helpers import so_digits
from core.models import Cliente
from routes.clientes_ligacoes.cache_invalidation import invalidar_caches_listagens_clientes
from routes.clientes_ligacoes.oracle_sync_helpers import aplicar_dados_oracle_no_cliente


def sincronizar_cliente_oracle_por_id_service(cliente_id, payload):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente:
        return {"ok": False, "mensagem": "Cliente nao encontrado"}, 404

    cnpj = so_digits(payload.get("cnpj") or cliente.cnpj)
    if not cnpj:
        return {"ok": False, "mensagem": "Cliente sem CNPJ para sincronizar com Oracle"}, 400

    from oracle_service import get_cliente_oracle_por_cnpj

    cliente_oracle = get_cliente_oracle_por_cnpj(cnpj)
    if not cliente_oracle:
        return {"ok": False, "mensagem": "Cliente nao encontrado no Oracle para este CNPJ"}, 404

    aplicar_dados_oracle_no_cliente(cliente, cliente_oracle)
    db.session.add(cliente)
    db.session.commit()
    invalidar_caches_listagens_clientes("sincronizacao de cliente com oracle")

    return {
        "ok": True,
        "mensagem": "Cliente sincronizado com Oracle",
        "cliente": {
            "id": cliente.id,
            "cd_cliente_oracle": cliente.cd_cliente_oracle,
            "cnpj": cliente.cnpj,
            "nome": cliente.nome,
            "telefone": cliente.telefone,
        },
    }, 200


def sincronizar_clientes_manuais_oracle_service():
    from oracle_service import get_cliente_oracle_por_cnpj

    logger = logging.getLogger(__name__)
    # Regra de negocio: "Clientes Especiais" = manual + importado_csv.
    # Aqui sincronizamos apenas os especiais ainda sem vinculo Oracle para
    # evitar reprocessamento desnecessario em massa.
    clientes_manuais = (
        Cliente.query
        .filter(
            Cliente.ativo == True,
            Cliente.origem.in_(("manual", "importado_csv")),
            Cliente.cnpj.isnot(None),
            or_(
                Cliente.cd_cliente_oracle.is_(None),
                Cliente.data_ultima_sincronizacao.is_(None),
            ),
        )
        .all()
    )

    total_base = len(clientes_manuais)
    logger.info(f"[Sync Manuais] Total de clientes manuais com CNPJ: {total_base}")

    atualizados = 0
    nao_encontrados = 0
    sem_cnpj = 0
    lista_nao_encontrados = []

    for cliente in clientes_manuais:
        cnpj = so_digits(cliente.cnpj)
        if not cnpj:
            sem_cnpj += 1
            continue

        logger.info(f"[Sync Manuais] Buscando cliente '{cliente.nome}' com CNPJ: {cnpj}")

        row = None
        max_tentativas = 3
        for tentativa in range(max_tentativas):
            try:
                row = get_cliente_oracle_por_cnpj(cnpj)
                if row:
                    break
                break
            except Exception as e:
                logger.warning(f"[Sync Manuais] Erro na tentativa {tentativa+1} para {cnpj}: {e}")
                if tentativa < max_tentativas - 1:
                    time.sleep(0.5)

        if not row:
            nao_encontrados += 1
            lista_nao_encontrados.append(f"{cliente.nome} ({cnpj})")
            logger.warning(f"[Sync Manuais] NÃO ENCONTRADO: {cliente.nome} - CNPJ: {cnpj}")
            continue

        logger.info(f"[Sync Manuais] ENCONTRADO: {cliente.nome} -> cd_cliente: {row.get('cd_cliente')}")
        aplicar_dados_oracle_no_cliente(cliente, row)
        db.session.add(cliente)
        atualizados += 1

    db.session.commit()
    invalidar_caches_listagens_clientes("sincronizacao em lote de clientes manuais com oracle")

    logger.info(
        f"[Sync Manuais] RESUMO: Total={total_base}, Atualizados={atualizados}, "
        f"NaoEncontrados={nao_encontrados}, SemCNPJ={sem_cnpj}"
    )
    if lista_nao_encontrados:
        logger.info(f"[Sync Manuais] Lista nao encontrados: {', '.join(lista_nao_encontrados[:10])}")

    return {
        "ok": True,
        "mensagem": (
            f"Sync manuais concluida. Base: {total_base} | "
            f"Atualizados: {atualizados} | "
            f"Nao encontrados no Oracle: {nao_encontrados} | "
            f"Sem CNPJ valido: {sem_cnpj}"
        ),
        "total_base": total_base,
        "atualizados": atualizados,
        "nao_encontrados": nao_encontrados,
        "sem_cnpj": sem_cnpj,
        "nao_encontrados_lista": lista_nao_encontrados[:20],
    }, 200
