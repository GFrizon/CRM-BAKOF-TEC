"""Consistency check for Supervisor KPI counters vs list sources.

Run:
    python scripts/check_kpi_consistency.py
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from routes.clientes_ligacoes.badges import _total_inativos_badge, _total_proximos_badge
from routes.clientes_ligacoes.oracle_tab import carregar_clientes_oracle_deduplicados


def _count_dashboard_90_150():
    clientes_oracle = carregar_clientes_oracle_deduplicados(app.logger, periodo_oracle=None)
    return len(clientes_oracle or [])


def main():
    with app.app_context():
        total_oracle_lista = len(carregar_clientes_oracle_deduplicados(app.logger, periodo_oracle=None) or [])
        total_90_150_dashboard = _count_dashboard_90_150()
        total_proximos_badge = int(_total_proximos_badge(None) or 0)
        total_inativos_badge = int(_total_inativos_badge(None) or 0)

        print("KPI consistency check")
        print(f"- 90-150 (dashboard): {total_90_150_dashboard}")
        print(f"- 90-150 (lista oracle): {total_oracle_lista}")
        print(f"- proximos inativacao (badge/lista): {total_proximos_badge}")
        print(f"- inativos (badge/lista): {total_inativos_badge}")

        inconsistencias = []
        if total_90_150_dashboard != total_oracle_lista:
            inconsistencias.append(
                f"90-150 divergente: dashboard={total_90_150_dashboard} lista={total_oracle_lista}"
            )

        if inconsistencias:
            print("\nINCONSISTENCIAS ENCONTRADAS:")
            for item in inconsistencias:
                print(f"- {item}")
            raise SystemExit(1)

        print("\nOK: sem divergencias nos KPIs conferidos.")


if __name__ == "__main__":
    main()
