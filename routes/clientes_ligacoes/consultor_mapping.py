from core.models import Usuario
from routes.clientes_ligacoes.domain_utils import _normalizar_nome_consultor

CODIGOS_REFERENCIA_CONSULTOR = {
    "100": "Roseleia Basso",
    "002": "Rodrigo Crespan",
    "007": "Janine de Mello",
    "012": "Sandra Vendruscolo da Silva",
    "001": "Elisabete Haus",
    "003": "Iara Sponchiado",
    "004": "Odete Luza",
    "005": "Carla Siduoski",
    "006": "Sibele Froner",
    "010": "Sibele Froner",
    "999": "Daniela Da Rosa",
}


def construir_mapa_codigo_para_id(mapa_nome_para_id: dict) -> dict:
    mapa_codigo_para_id = {}
    for codigo, nome_ref in CODIGOS_REFERENCIA_CONSULTOR.items():
        uid = mapa_nome_para_id.get(_normalizar_nome_consultor(nome_ref))
        if uid:
            mapa_codigo_para_id[codigo] = uid
    return mapa_codigo_para_id


def carregar_mapa_nome_para_id_usuarios_ativos():
    usuarios_ativos = Usuario.query.filter(
        Usuario.ativo == True,
        Usuario.tipo.in_(["consultor", "televendas", "supervisor"]),
    ).all()
    mapa_nome_para_id = {
        _normalizar_nome_consultor(u.nome): u.id
        for u in usuarios_ativos if u and u.nome
    }
    return usuarios_ativos, mapa_nome_para_id
