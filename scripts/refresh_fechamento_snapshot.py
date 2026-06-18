import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import app
from routes.clientes_ligacoes.analytics_api import consultar_resultados_consultores_mes


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Recalcula e persiste o snapshot de fechamento sem rodar a sincronizacao completa."
    )
    parser.add_argument("--mes", type=int, required=True, help="Mes do fechamento (1-12)")
    parser.add_argument("--ano", type=int, required=True, help="Ano do fechamento")
    parser.add_argument(
        "--tipo",
        default="consultor",
        choices=["consultor", "televendas"],
        help="Tipo de operador do fechamento",
    )
    parser.add_argument(
        "--meta-conversao",
        type=float,
        default=10.0,
        help="Meta de conversao usada no fechamento",
    )
    parser.add_argument(
        "--resumo",
        action="store_true",
        help="Exibe apenas um resumo enxuto em vez do payload completo.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()

    with app.app_context():
        payload, status = consultar_resultados_consultores_mes(
            args.mes,
            args.ano,
            meta_conversao=args.meta_conversao,
            tipo_operador=args.tipo,
        )

    if status != 200 or not payload.get("ok"):
        erro = payload.get("erro") or f"Falha ao recalcular fechamento ({status})"
        print(erro, file=sys.stderr)
        return 1

    if args.resumo:
        totais = payload.get("totais") or {}
        snapshot = payload.get("snapshot_info") or {}
        saida = {
            "periodo": f"{int(args.ano):04d}-{int(args.mes):02d}",
            "tipo": args.tipo,
            "total_ligacoes": totais.get("total_ligacoes"),
            "total_vendas": totais.get("total_vendas"),
            "total_retornar": totais.get("total_retornar"),
            "total_90_150": totais.get("total_90_150"),
            "total_proximos_inativacao": totais.get("total_proximos_inativacao"),
            "historico_90_150_data_ref": totais.get("historico_90_150_data_ref"),
            "historico_proximos_data_ref": totais.get("historico_proximos_data_ref"),
            "snapshot_info": snapshot,
        }
        print(json.dumps(saida, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
