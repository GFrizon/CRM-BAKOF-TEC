from routes.clientes_ligacoes.import_helpers import extrair_campos_linha, validar_campos_linha
from routes.clientes_ligacoes.import_persistence import (
    atualizar_ou_reativar_cliente_importado,
    construir_cliente_importado,
    flush_batch_clientes,
    flush_batch_final_clientes,
    registrar_importacao,
)
from core.extensions import db


def processar_importacao_dataframe(df, consultor_id, logger, get_pos_fn, normalizar_texto_fn, so_digits_fn):
    total_inseridos, pulados = 0, 0
    erros = []
    batch_size = 100
    batch_clientes = []

    logger.info(f"Iniciando importação de {len(df)} registros")

    for i, row in df.iterrows():
        try:
            campos = extrair_campos_linha(row, get_pos_fn, normalizar_texto_fn, so_digits_fn)
            empresa_cnpj = campos.get("empresa_cnpj")
            representante = campos.get("representante")
            nome_cliente = campos.get("nome_cliente")
            valido, telefone, erro_validacao = validar_campos_linha(campos)
            if not valido:
                if erro_validacao:
                    if erro_validacao.startswith("Nome muito curto"):
                        logger.warning(f"Linha {i+2}: Nome do cliente muito curto: '{nome_cliente}'")
                    elif erro_validacao.startswith("CNPJ inválido"):
                        logger.warning(f"Linha {i+2}: CNPJ inválido: {empresa_cnpj}")
                    erros.append(f"Linha {i+2}: {erro_validacao}")
                    pulados += 1
                continue

            if empresa_cnpj:
                try:
                    acao = atualizar_ou_reativar_cliente_importado(
                        empresa_cnpj=empresa_cnpj,
                        nome_cliente=nome_cliente,
                        telefone=telefone,
                        representante=representante,
                        consultor_id=consultor_id,
                    )
                    if acao in ("atualizado", "reativado"):
                        total_inseridos += 1
                        continue
                    if acao == "inalterado":
                        pulados += 1
                        continue
                except Exception as db_error:
                    logger.error(f"Erro de banco ao processar linha {i+2}: {str(db_error)}")
                    erros.append(f"Linha {i+2}: Erro de banco - {str(db_error)}")
                    continue

            try:
                novo = construir_cliente_importado(
                    nome_cliente=nome_cliente,
                    empresa_cnpj=empresa_cnpj,
                    telefone=telefone,
                    representante=representante,
                    consultor_id=consultor_id,
                )
                batch_clientes.append(novo)
                total_inseridos += 1

                if len(batch_clientes) >= batch_size:
                    batch_clientes = flush_batch_clientes(batch_clientes, logger, erros)

            except ValueError as val_error:
                logger.warning(f"Valor inválido na linha {i+2}: {str(val_error)}")
                erros.append(f"Linha {i+2}: Valor inválido - {str(val_error)}")
                continue
            except Exception as create_error:
                logger.error(f"Erro ao criar cliente linha {i+2}: {str(create_error)}")
                erros.append(f"Linha {i+2}: Erro criação - {str(create_error)}")
                continue

        except IndexError as idx_error:
            logger.warning(f"Linha {i+2} com formato inválido: {str(idx_error)}")
            erros.append(f"Linha {i+2}: Formato inválido - colunas insuficientes")
            continue
        except Exception as e:
            logger.error(f"Erro inesperado na linha {i+2}: {str(e)}")
            erros.append(f"Linha {i+2}: {str(e)}")
            continue

    flush_batch_final_clientes(batch_clientes, logger, erros)
    return {
        "total_inseridos": total_inseridos,
        "pulados": pulados,
        "erros": erros,
    }


def executar_importacao_completa(
    df,
    filename,
    consultor_id,
    logger,
    get_pos_fn,
    normalizar_texto_fn,
    so_digits_fn,
):
    resumo = processar_importacao_dataframe(
        df=df,
        consultor_id=consultor_id,
        logger=logger,
        get_pos_fn=get_pos_fn,
        normalizar_texto_fn=normalizar_texto_fn,
        so_digits_fn=so_digits_fn,
    )

    total_inseridos = int(resumo.get("total_inseridos") or 0)
    pulados = int(resumo.get("pulados") or 0)
    erros = list(resumo.get("erros") or [])
    registrar_importacao(filename, consultor_id, total_inseridos, logger)

    try:
        db.session.commit()
        logger.info(
            f"Importação concluída: {total_inseridos} inseridos, {pulados} pulados, {len(erros)} erros"
        )
        return {
            "ok": True,
            "total_inseridos": total_inseridos,
            "pulados": pulados,
            "erros": erros,
        }
    except Exception as commit_error:
        logger.error(f"Erro no commit final: {str(commit_error)}")
        db.session.rollback()
        return {
            "ok": False,
            "erro_commit": str(commit_error),
            "total_inseridos": total_inseridos,
            "pulados": pulados,
            "erros": erros,
        }
