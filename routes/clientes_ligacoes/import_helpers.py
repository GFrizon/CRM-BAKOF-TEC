import pandas as pd


COL_TIPO = 0
COL_EMPRESA_CNPJ = 1
COL_CONSULTOR_TXT = 2
COL_REPRESENTANTE = 3
COL_NOME_CLIENTE = 4
COL_TELEFONE = 5


def carregar_dataframe_importacao(arquivo, ext):
    df = None
    try:
        if ext in ("xlsx", "xls") or not ext:
            df = pd.read_excel(
                arquivo,
                dtype=str,
                header=0,
                keep_default_na=False,
                na_filter=False,
                engine="openpyxl",
            )
        else:
            raise ValueError("not excel")
    except Exception:
        try:
            arquivo.seek(0)
        except Exception:
            pass
        try:
            df = pd.read_csv(
                arquivo,
                sep=";",
                dtype=str,
                encoding="utf-8",
                keep_default_na=False,
                na_filter=False,
            )
        except UnicodeDecodeError:
            arquivo.seek(0)
            df = pd.read_csv(
                arquivo,
                sep=";",
                dtype=str,
                encoding="latin1",
                keep_default_na=False,
                na_filter=False,
            )
    return df


def extrair_campos_linha(row, get_pos_fn, normalizar_texto_fn, so_digits_fn):
    tipo = normalizar_texto_fn(get_pos_fn(row, COL_TIPO))
    empresa_cnpj = so_digits_fn(get_pos_fn(row, COL_EMPRESA_CNPJ))
    consultor_txt = normalizar_texto_fn(get_pos_fn(row, COL_CONSULTOR_TXT))
    representante = normalizar_texto_fn(get_pos_fn(row, COL_REPRESENTANTE))
    nome_cliente = normalizar_texto_fn(get_pos_fn(row, COL_NOME_CLIENTE))

    raw_tel = get_pos_fn(row, COL_TELEFONE)
    if not normalizar_texto_fn(raw_tel):
        try:
            for colname, val in row.items():
                if colname and "tel" in str(colname).lower():
                    raw_tel = val
                    break
        except Exception:
            pass
    telefone = so_digits_fn(raw_tel) or None

    return {
        "tipo": tipo,
        "empresa_cnpj": empresa_cnpj,
        "consultor_txt": consultor_txt,
        "representante": representante,
        "nome_cliente": nome_cliente,
        "telefone": telefone,
    }


def validar_campos_linha(campos):
    nome_cliente = campos.get("nome_cliente")
    empresa_cnpj = campos.get("empresa_cnpj")
    telefone = campos.get("telefone")

    if nome_cliente and len(nome_cliente.strip()) < 2:
        return False, telefone, "Nome muito curto (mínimo 2 caracteres)"

    if empresa_cnpj and len(empresa_cnpj) < 11:
        return False, telefone, "CNPJ inválido (mínimo 11 dígitos)"

    if telefone and (len(telefone) < 10 or len(telefone) > 11):
        telefone = None

    campos_preenchidos = [
        campos.get("tipo"),
        empresa_cnpj,
        campos.get("consultor_txt"),
        campos.get("representante"),
        nome_cliente,
        telefone,
    ]
    if not any(campos_preenchidos):
        return False, telefone, None

    if not nome_cliente:
        return False, telefone, "Nome vazio"

    return True, telefone, None
