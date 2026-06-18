import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import app
from core.models import Cliente
from services.carteiras_movimento_service import atualizar_codigos_presentes_movimento_carteira


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Preenche codigos_presentes nas carteiras diarias sem rodar sincronizacao completa."
    )
    parser.add_argument(
        "--data",
        default=datetime.now().date().isoformat(),
        help="Data de referencia no formato YYYY-MM-DD",
    )
    return parser.parse_args()


def _coletar_codigos_por_faixa(data_ref):
    limite_90 = data_ref - timedelta(days=90)
    limite_150 = data_ref - timedelta(days=150)
    limite_151 = data_ref - timedelta(days=151)
    limite_180 = data_ref - timedelta(days=180)

    base = [
        Cliente.ativo == True,
        Cliente.cd_cliente_oracle.isnot(None),
        Cliente.ultimo_pedido_oracle.isnot(None),
    ]

    oracle_90_150 = {
        str(c.cd_cliente_oracle).strip()
        for c in Cliente.query.filter(
            *base,
            Cliente.ultimo_pedido_oracle.between(limite_150, limite_90),
        ).all()
        if c.cd_cliente_oracle
    }
    proximos = {
        str(c.cd_cliente_oracle).strip()
        for c in Cliente.query.filter(
            *base,
            Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
        ).all()
        if c.cd_cliente_oracle
    }
    return oracle_90_150, proximos


def main():
    args = _parse_args()
    data_ref = datetime.fromisoformat(str(args.data)).date()

    with app.app_context():
        oracle_90_150, proximos = _coletar_codigos_por_faixa(data_ref)
        ok_90 = atualizar_codigos_presentes_movimento_carteira(
            "oracle_90_150",
            data_ref,
            oracle_90_150,
        )
        ok_px = atualizar_codigos_presentes_movimento_carteira(
            "proximos_inativacao",
            data_ref,
            proximos,
        )

    if not ok_90 and not ok_px:
        print("Nenhum registro diario encontrado para a data informada.", file=sys.stderr)
        return 1

    print(
        f"Baseline preenchida em {data_ref.isoformat()}: "
        f"90-150={len(oracle_90_150)} | proximos={len(proximos)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
