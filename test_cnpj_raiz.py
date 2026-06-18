"""
Script manual para verificar busca por CNPJ Raiz.
"""

from oracle_service import get_cliente_oracle_por_cnpj


def main():
    print("=" * 60)
    print("Teste 1: Buscando por CNPJ completo")
    cnpj_completo = input("Digite um CNPJ completo (14 dígitos) que existe no Oracle: ")
    resultado = get_cliente_oracle_por_cnpj(cnpj_completo)
    if resultado:
        print(f"✓ Cliente encontrado: {resultado.get('cliente')}")
        print(f"  CNPJ: {resultado.get('cnpj')}")
        print(f"  Representante: {resultado.get('representante')}")
    else:
        print("✗ Cliente não encontrado")

    print("\n" + "=" * 60)
    print("Teste 2: Buscando por CNPJ Raiz (8 primeiros dígitos)")
    cnpj_raiz = input("Digite apenas os 8 primeiros dígitos do CNPJ: ")
    resultado_raiz = get_cliente_oracle_por_cnpj(cnpj_raiz)
    if resultado_raiz:
        print(f"✓ Cliente encontrado via CNPJ Raiz: {resultado_raiz.get('cliente')}")
        print(f"  CNPJ completo no Oracle: {resultado_raiz.get('cnpj')}")
        print(f"  Representante: {resultado_raiz.get('representante')}")
    else:
        print("✗ Cliente não encontrado via CNPJ Raiz")

    print("\n" + "=" * 60)
    print("Teste 3: CNPJ completo inexistente (deve buscar por raiz)")
    cnpj_fake = input("Digite um CNPJ completo que não existe (mas a raiz existe): ")
    resultado_fallback = get_cliente_oracle_por_cnpj(cnpj_fake)
    if resultado_fallback:
        print(f"✓ Cliente encontrado via fallback CNPJ Raiz: {resultado_fallback.get('cliente')}")
        print(f"  CNPJ completo no Oracle: {resultado_fallback.get('cnpj')}")
        print(f"  Representante: {resultado_fallback.get('representante')}")
    else:
        print("✗ Cliente não encontrado nem via CNPJ Raiz")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
