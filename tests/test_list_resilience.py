import inspect
import json
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask
from flask_login import LoginManager, UserMixin

from routes.clientes_ligacoes import ativos_tab
from routes.clientes_ligacoes import listagem_ativos
from routes.clientes_ligacoes import listagem_routes
from routes.clientes_ligacoes.continuidade_compra import (
    enriquecer_payloads_com_continuidade_compra,
)
from services.daily_snapshot_history import salvar_snapshot_em_historico
from sincronizacao_automatica import sincronizacao_automatica_diaria
from services import warmup_service
from routes.clientes_ligacoes.html_response_cache import (
    limpar_cache_html_listagens,
    register_html_response_cache,
)


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _CacheUser(UserMixin):
    def __init__(self, user_id):
        self.id = int(user_id)
        self.tipo = "supervisor"
        self.viu_novidades = True


class ListResilienceTests(unittest.TestCase):
    def test_sync_receives_existing_flask_app(self):
        self.assertEqual(str(inspect.signature(sincronizacao_automatica_diaria)), "(app)")
        source = inspect.getsource(sincronizacao_automatica_diaria)
        self.assertNotIn("from app import app", source)
        self.assertIn("salvar_snapshot_ativos_oracle", source)
        self.assertIn("salvar_snapshot_oracle_90_150", source)
        self.assertIn("salvar_snapshot_proximos_inativacao", source)
        self.assertIn("salvar_snapshot_inativos_oracle", source)
        self.assertIn("salvar_snapshot_construtoras_oracle", source)

    def test_general_list_invalidation_clears_representative_dashboard(self):
        listagem_routes._INATIVOS_COUNT_CACHE["teste"] = object()
        listagem_routes._REPRESENTANTE_DASHBOARD_CACHE["970081"] = {
            "ts": object(),
            "total_pendentes": 10,
            "total_retornar": 2,
        }

        listagem_routes.limpar_cache_contagem_inativos()

        self.assertEqual(listagem_routes._INATIVOS_COUNT_CACHE, {})
        self.assertEqual(listagem_routes._REPRESENTANTE_DASHBOARD_CACHE, {})

    def test_ativos_uses_snapshot_without_querying_oracle(self):
        snapshot = {
            "itens": [
                {
                    "cd_cliente": "123",
                    "cliente": "Cliente teste",
                    "dt_pedido": "2026-06-18T08:00:00",
                }
            ]
        }
        oracle_fake = types.ModuleType("oracle_service")
        oracle_fake.get_clientes_ativos_oracle = Mock(
            side_effect=AssertionError("Oracle nao deve ser consultado com snapshot valido")
        )

        ativos_tab.limpar_cache_clientes_ativos()
        with (
            patch.dict(sys.modules, {"oracle_service": oracle_fake}),
            patch.object(ativos_tab, "carregar_snapshot_ativos_oracle", return_value=snapshot),
            patch.object(ativos_tab, "salvar_snapshot_ativos_oracle") as salvar,
        ):
            resultado = ativos_tab.carregar_clientes_ativos_oracle_deduplicados(_Logger())

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["cd_cliente"], "123")
        oracle_fake.get_clientes_ativos_oracle.assert_not_called()
        salvar.assert_not_called()

    def test_snapshot_write_is_atomic_under_threads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            inicio = date(2026, 1, 1)

            def gravar(indice):
                salvar_snapshot_em_historico(
                    path,
                    {"total": indice, "itens": [{"id": indice}]},
                    data_ref=inicio + timedelta(days=indice),
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(gravar, range(30)))

            storage = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(storage["snapshots"]), 30)

            conteudo_valido = path.read_bytes()
            with self.assertRaises(TypeError):
                salvar_snapshot_em_historico(
                    path,
                    {"valor_invalido": object()},
                    data_ref=date(2026, 3, 1),
                )
            self.assertEqual(path.read_bytes(), conteudo_valido)

    def test_continuidade_uses_persisted_metrics_cache(self):
        payloads = [{"cd_cliente_oracle": "123"}]
        mapa = {"123": [(2026, 5), (2026, 6)]}
        alvo = (
            "services.representante_metricas_cache_service."
            "carregar_meses_compra_representante"
        )
        with patch(alvo, return_value=mapa) as carregar:
            enriquecer_payloads_com_continuidade_compra(
                payloads,
                periodo="ano_atual",
            )

        carregar.assert_called_once_with(["123"], periodo="ano_atual")
        self.assertEqual(payloads[0]["meses_com_compra"], 2)

    def test_warmup_prepares_periods_used_by_lists(self):
        app_fake = types.SimpleNamespace(app_context=lambda: _NullContext())
        with (
            patch.object(warmup_service, "_coletar_todos_codigos_snapshots", return_value={"123"}),
            patch(
                "services.representante_metricas_cache_service."
                "carregar_meses_compra_representante"
            ) as carregar,
        ):
            warmup_service._aquece_meses_compra(app_fake)

        periodos = [chamada.kwargs["periodo"] for chamada in carregar.call_args_list]
        self.assertEqual(
            periodos,
            ["ano_atual", "ultimos_365_dias", "ultimos_3_anos"],
        )

    def test_local_ativos_cache_can_be_invalidated(self):
        listagem_ativos._ATIVOS_LOCAIS_CACHE[frozenset({"123"})] = {
            "ts": 1,
            "data": [object()],
        }
        listagem_ativos.limpar_cache_locais_ativos()
        self.assertEqual(listagem_ativos._ATIVOS_LOCAIS_CACHE, {})

    def test_html_cache_isolated_by_user_and_query(self):
        limpar_cache_html_listagens()
        app = Flask(__name__)
        app.secret_key = "teste"
        login_manager = LoginManager(app)
        chamadas = {"total": 0}

        @login_manager.user_loader
        def carregar_usuario(user_id):
            return _CacheUser(user_id)

        register_html_response_cache(app)

        @app.get("/meus-clientes")
        def meus_clientes_teste():
            chamadas["total"] += 1
            return f"render-{chamadas['total']}"

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        primeira = client.get("/meus-clientes?visao=clientes&aba=ativos")
        segunda = client.get("/meus-clientes?visao=clientes&aba=ativos")
        outro_filtro = client.get(
            "/meus-clientes?visao=clientes&aba=ativos&q=abc"
        )

        self.assertEqual(primeira.headers.get("X-List-Cache"), "MISS")
        self.assertEqual(segunda.headers.get("X-List-Cache"), "HIT")
        self.assertEqual(primeira.data, segunda.data)
        self.assertEqual(outro_filtro.headers.get("X-List-Cache"), "MISS")
        self.assertEqual(chamadas["total"], 2)

        with client.session_transaction() as sess:
            sess["_user_id"] = "2"
            sess["_fresh"] = True
        outro_usuario = client.get("/meus-clientes?visao=clientes&aba=ativos")
        self.assertEqual(outro_usuario.headers.get("X-List-Cache"), "MISS")
        self.assertEqual(chamadas["total"], 3)

        limpar_cache_html_listagens()
        apos_invalidacao = client.get("/meus-clientes?visao=clientes&aba=ativos")
        self.assertEqual(apos_invalidacao.headers.get("X-List-Cache"), "MISS")
        self.assertEqual(chamadas["total"], 4)

    def test_snapshot_retention_keeps_recent_days_and_monthly_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            inicio = date(2025, 1, 1)
            for indice in range(500):
                salvar_snapshot_em_historico(
                    path,
                    {"total": indice, "itens": []},
                    data_ref=inicio + timedelta(days=indice),
                    dias_detalhados=31,
                    meses_historicos=24,
                )

            storage = json.loads(path.read_text(encoding="utf-8"))
            chaves = sorted(storage["snapshots"])
            recentes = [
                chave
                for chave in chaves
                if date.fromisoformat(chave) >= inicio + timedelta(days=468)
            ]
            antigos_por_mes = {}
            for chave in chaves:
                data_chave = date.fromisoformat(chave)
                if data_chave < inicio + timedelta(days=468):
                    antigos_por_mes.setdefault(chave[:7], []).append(chave)

            self.assertGreaterEqual(len(recentes), 31)
            self.assertTrue(all(len(datas) == 1 for datas in antigos_por_mes.values()))
            self.assertLess(len(chaves), 60)


if __name__ == "__main__":
    unittest.main()
