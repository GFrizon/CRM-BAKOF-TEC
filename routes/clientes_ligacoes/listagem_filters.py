def extrair_filtros_listagem(request):
    conceito_filtro = (request.args.get("conceito_filtro") or "").strip().upper()
    consultor_filtro = (request.args.get("consultor_filtro") or "").strip()
    termo = (request.args.get("q") or "").strip().lower()
    return conceito_filtro, consultor_filtro, termo


def corresponde_conceito_filtro(conceito_filtro: str, conceito_cliente: str) -> bool:
    if not conceito_filtro:
        return True
    if conceito_filtro in ("SEM_CONCEITO", "SEM CONCEITO"):
        return conceito_cliente in ("", "SEM CONCEITO")
    return conceito_cliente == conceito_filtro


def corresponde_consultor_filtro(consultor_filtro: str, consultor_cliente: str) -> bool:
    if not consultor_filtro:
        return True
    return consultor_filtro.lower() in consultor_cliente.lower()


def corresponde_termo_busca(termo: str, registro: dict, campos_busca) -> bool:
    if not termo:
        return True
    base_busca = " ".join(str(registro.get(campo) or "") for campo in campos_busca).lower()
    return termo in base_busca
