from datetime import datetime

from core.helpers import s, so_digits
from core.models import Usuario
from routes.clientes_ligacoes.domain_utils import (
    _extrair_nome_oracle_consultor,
    _normalizar_nome_consultor,
)


def sugerir_consultor_por_categoria_oracle(categoria_oracle):
    consultor_oracle = s(categoria_oracle)
    nome_oracle = _extrair_nome_oracle_consultor(consultor_oracle)
    nome_oracle_norm = _normalizar_nome_consultor(nome_oracle)
    if not nome_oracle_norm:
        return None

    candidatos = Usuario.query.filter(
        Usuario.tipo.in_(["consultor", "televendas"]),
        Usuario.ativo == True,
    ).all()
    mapa_nome = {
        _normalizar_nome_consultor(u.nome): u
        for u in candidatos
        if u and u.nome
    }
    consultor_sugerido = mapa_nome.get(nome_oracle_norm)
    if consultor_sugerido:
        return consultor_sugerido

    primeiro_oracle = nome_oracle_norm.split()[0]
    for nome_norm, usuario in mapa_nome.items():
        if nome_norm and nome_norm.split()[0] == primeiro_oracle:
            return usuario
    return None


def aplicar_dados_oracle_no_cliente(cliente, row):
    nome_oracle = s(row.get("cliente")) or None
    telefone1 = so_digits(row.get("telefone1")) or None
    telefone2 = so_digits(row.get("telefone2")) or None
    representante = s(row.get("representante")) or None

    cliente.cnpj = so_digits(row.get("cnpj")) or cliente.cnpj
    if nome_oracle and (not cliente.nome or cliente.nome.strip() == ""):
        cliente.nome = nome_oracle[:200]
    if telefone1:
        cliente.telefone = telefone1
    if telefone2:
        cliente.telefone2 = telefone2
    if representante:
        cliente.representante_nome = representante
        cliente.representante_oracle = representante

    cliente.cd_cliente_oracle = str(row.get("cd_cliente") or "").strip() or cliente.cd_cliente_oracle
    cliente.categoria_consultor = s(row.get("consultor")) or cliente.categoria_consultor
    cliente.conceito = s(row.get("conceito")) or cliente.conceito
    cliente.municipio = s(row.get("municipio")) or cliente.municipio
    cliente.uf = s(row.get("uf")) or cliente.uf
    cliente.contato = s(row.get("contato")) or cliente.contato
    cliente.data_ultima_sincronizacao = datetime.now()


def montar_payload_cliente_oracle(cnpj_consultado, row, consultor_sugerido):
    return {
        "cd_cliente_oracle": str(row.get("cd_cliente") or "").strip(),
        "nome": s(row.get("cliente")),
        "cnpj": so_digits(row.get("cnpj")) or cnpj_consultado,
        "telefone": so_digits(row.get("telefone1")) or "",
        "telefone2": so_digits(row.get("telefone2")) or "",
        "representante_nome": s(row.get("representante")),
        "representante_oracle": s(row.get("representante")),
        "categoria_consultor": s(row.get("consultor")),
        "conceito": s(row.get("conceito")),
        "municipio": s(row.get("municipio")),
        "uf": s(row.get("uf")),
        "contato": s(row.get("contato")),
        "consultor_id_sugerido": (consultor_sugerido.id if consultor_sugerido else None),
        "consultor_nome_sugerido": (consultor_sugerido.nome if consultor_sugerido else None),
    }
