from routes.clientes_ligacoes.domain_utils import (
    _codigo_representante_de_texto,
    _normalizar_codigo_representante,
    _resolver_consultor_id_por_categoria,
)


def representante_oracle_permitido_para_usuario(
    *,
    tipo_usuario: str,
    representante_texto: str,
    codigos_representantes_vinculados,
) -> bool:
    if tipo_usuario != "supervisor_repr":
        return True
    cd_representante = _normalizar_codigo_representante(
        _codigo_representante_de_texto(representante_texto)
    )
    return bool(cd_representante and cd_representante in codigos_representantes_vinculados)


def consultor_categoria_permitido_para_usuario(
    *,
    tipo_usuario: str,
    consultor_cliente: str,
    current_user_id: int,
    mapa_codigo_para_id: dict,
    mapa_nome_para_id: dict,
) -> bool:
    if tipo_usuario != "consultor":
        return True
    if not consultor_cliente:
        return True

    consultor_esperado = _resolver_consultor_id_por_categoria(
        consultor_cliente,
        mapa_codigo_para_id=mapa_codigo_para_id,
        mapa_nome_para_id=mapa_nome_para_id,
    )
    if not consultor_esperado:
        return True
    return consultor_esperado == current_user_id
