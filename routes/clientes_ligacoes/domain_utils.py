import unicodedata


def _normalizar_nome_consultor(txt: str) -> str:
    if not txt:
        return ""
    base = unicodedata.normalize("NFKD", str(txt))
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(base.lower().split())


def _normalizar_codigo_representante(codigo) -> str:
    valor = str(codigo or "").strip()
    if not valor:
        return ""
    if valor.isdigit():
        return str(int(valor))
    return valor.lower()


def _codigo_representante_de_texto(texto: str) -> str:
    valor = str(texto or "").strip()
    if not valor:
        return ""
    if " - " in valor:
        return valor.split(" - ")[-1].strip()
    return valor


def _cliente_tem_representante_vinculado(cliente, codigos_representantes_vinculados) -> bool:
    codigo_rep_cliente = _normalizar_codigo_representante(
        _codigo_representante_de_texto(cliente.representante_oracle or cliente.representante_nome)
    )
    return bool(codigo_rep_cliente and codigo_rep_cliente in codigos_representantes_vinculados)


def _extrair_nome_oracle_consultor(valor_oracle: str) -> str:
    # Ex.: "005 - CARLA - C42" -> "CARLA"
    if not valor_oracle:
        return ""
    partes = [p.strip() for p in str(valor_oracle).split("-") if p.strip()]
    if len(partes) >= 2:
        return partes[1]
    return partes[0] if partes else ""


def _resolver_consultor_id_por_categoria(
    categoria_oracle: str,
    mapa_codigo_para_id: dict,
    mapa_nome_para_id: dict,
):
    texto = str(categoria_oracle or "").strip()
    if not texto:
        return None

    codigo = ""
    if "-" in texto:
        codigo = texto.split("-", 1)[0].strip()
    elif " - " in texto:
        codigo = texto.split(" - ", 1)[0].strip()

    nome_oracle = _extrair_nome_oracle_consultor(texto)
    nome_norm = _normalizar_nome_consultor(nome_oracle)
    if nome_norm and nome_norm in mapa_nome_para_id:
        return mapa_nome_para_id[nome_norm]
    if codigo and codigo in mapa_codigo_para_id:
        return mapa_codigo_para_id[codigo]
    return None
