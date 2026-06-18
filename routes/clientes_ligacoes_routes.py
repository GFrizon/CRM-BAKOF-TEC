from routes.clientes_ligacoes.access_control import bloquear_escrita_perfil_somente_leitura
from routes.clientes_ligacoes.analytics_routes import (
    register_clientes_ligacoes_analytics_routes,
)
from routes.clientes_ligacoes.interactions_routes import (
    register_clientes_ligacoes_interactions_routes,
)
from routes.clientes_ligacoes.listagem_routes import (
    register_clientes_ligacoes_listagem_routes,
)
from routes.clientes_ligacoes.html_response_cache import (
    register_html_response_cache,
)
from routes.clientes_ligacoes.management_routes import (
    register_clientes_ligacoes_management_routes,
)
from routes.clientes_ligacoes.notes_routes import (
    register_clientes_ligacoes_notes_routes,
)


def register_clientes_ligacoes_routes(app):
    register_html_response_cache(app)
    register_clientes_ligacoes_analytics_routes(app)
    register_clientes_ligacoes_interactions_routes(app)
    register_clientes_ligacoes_listagem_routes(app)
    register_clientes_ligacoes_management_routes(app)
    register_clientes_ligacoes_notes_routes(app)

    @app.before_request
    def _bloquear_escrita_perfil_somente_leitura_clientes():
        return bloquear_escrita_perfil_somente_leitura()
