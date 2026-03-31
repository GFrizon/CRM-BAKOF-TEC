#!/usr/bin/env python3
"""
Utilitários para padronização de números de telefone
Formato MicroSIP: (DDD) XXXXXXXX
"""

import re
from typing import Optional

def padronizar_telefone(telefone: str, ddd_padrao: str = None) -> Optional[str]:
    """
    Padroniza número de telefone para o formato do MicroSIP
    Lida com os diferentes padrões brasileiros
    
    Args:
        telefone: Número de telefone em qualquer formato
        ddd_padrao: DDD padrão para usar caso o telefone não tenha DDD
        
    Returns:
        Número padronizado ou None se inválido
    """
    if not telefone:
        return None
    
    # Remover todos os caracteres não numéricos
    numeros = re.sub(r'\D', '', str(telefone))
    
    # Remover zeros à esquerda excessivos (ex: 055 -> 55)
    if len(numeros) > 10 and numeros.startswith('0'):
        numeros = numeros[1:] if len(numeros) - 1 >= 10 else numeros
    
    # Caso especial: telefone local sem DDD (8 ou 9 dígitos)
    if len(numeros) in [8, 9] and ddd_padrao:
        # Adicionar DDD padrão
        numeros = ddd_padrao + numeros
    
    # Validar comprimento mínimo (10 dígitos: DDD + número)
    if len(numeros) < 10:
        return None
    
    # Extrair DDD (sempre os 2 primeiros dígitos após validação)
    ddd = numeros[:2]
    numero = numeros[2:]
    
    # Remover zeros à esquerda do número
    numero = numero.lstrip('0')
    
    # Validar e identificar tipo de telefone
    if len(numero) == 8:
        # Pode ser fixo ou celular antigo (sem 9)
        # Prefixos mais comuns de fixos no Brasil
        prefixos_fixos_comuns = ['2', '3', '4', '5']
        
        if numero[0] in prefixos_fixos_comuns:
            # Provavelmente é fixo
            return f"(0{ddd}) {numero}"
        else:
            # Provavelmente é celular antigo, adicionar 9
            return f"(0{ddd}) 9{numero}"
    elif len(numero) == 9:
        # Telefone com 9 dígitos
        if numero.startswith('9'):
            # Celular padrão
            return f"(0{ddd}) {numero}"
        else:
            # Pode ser erro ou formato especial
            # Vamos manter como está, mas marcar como desconhecido
            return f"(0{ddd}) {numero}"
    elif len(numero) == 10:
        # Caso raro: número com 10 dígitos (pode ser erro)
        # Vamos tentar corrigir removendo o primeiro dígito se for 9
        if numero.startswith('9'):
            return f"(0{ddd}) {numero[1:]}"  # Remove o 9 extra
        else:
            # Manter como está, mas pode ser inválido
            return f"(0{ddd}) {numero}"
    else:
        # Comprimento inválido
        return None

def identificar_ddd_padrao(telefones: list) -> Optional[str]:
    """
    Identifica o DDD mais comum em uma lista de telefones
    
    Args:
        telefones: Lista de números de telefone em qualquer formato
        
    Returns:
        DDD mais comum ou None
    """
    ddds = {}
    
    for telefone in telefones:
        if not telefone:
            continue
            
        # Extrair apenas números
        numeros = re.sub(r'\D', '', str(telefone))
        
        # Remover zeros à esquerda excessivos
        if len(numeros) > 10 and numeros.startswith('0'):
            numeros = numeros[1:] if len(numeros) - 1 >= 10 else numeros
        
        # Se tiver pelo menos 10 dígitos, extrair DDD
        if len(numeros) >= 10:
            ddd = numeros[:2]
            ddds[ddd] = ddds.get(ddd, 0) + 1
    
    if ddds:
        # Retornar o DDD mais frequente
        return max(ddds.items(), key=lambda x: x[1])[0]
    
    return None

def padronizar_telefone_com_ddd_padrao(telefone: str, telefones_base: list = None) -> Optional[str]:
    """
    Padroniza telefone usando DDD padrão detectado da base
    
    Args:
        telefone: Telefone a ser padronizado
        telefones_base: Lista de telefones para detectar DDD padrão
        
    Returns:
        Telefone padronizado
    """
    # Detectar DDD padrão se não fornecido
    ddd_padrao = None
    if telefones_base:
        ddd_padrao = identificar_ddd_padrao(telefones_base)
    
    return padronizar_telefone(telefone, ddd_padrao)

def identificar_tipo_telefone(telefone: str) -> str:
    """
    Identifica se o telefone é fixo ou celular
    
    Args:
        telefone: Número de telefone padronizado
        
    Returns:
        'fixo' ou 'celular'
    """
    if not telefone or '(' not in telefone:
        return 'desconhecido'
    
    # Extrair número após o DDD (com ou sem zero)
    # Padrão: (0XX) XXXXXXXX ou (XX) XXXXXXXX
    match = re.search(r'\(0\d{2}\)\s+(\d+)', telefone)
    if not match:
        return 'desconhecido'
    
    numero = match.group(1)
    
    if len(numero) == 8:
        return 'fixo'
    elif len(numero) == 9 and numero.startswith('9'):
        return 'celular'
    else:
        return 'desconhecido'

def testar_padronizacao():
    """Testa a função de padronização com vários exemplos"""
    
    testes = [
        # Formatos comuns que vêm do Oracle
        "055 996203010",
        "55 996203010", 
        "(055) 99620-3010",
        "55-99620-3010",
        "55.99620.3010",
        "55 99620 3010",
        "05537449900",
        "55 3744-9900",
        "(055) 3744-9900",
        "055-3744-9900",
        # Formatos problemáticos
        "0055996203010",
        "55055996203010",
        "55 9 9620-3010",
        "(55) 9 9620-3010",
        # Sem DDD (com DDD padrão 55)
        "996203010",
        "37449900",
        # Inválidos
        "123",
        "",
        None,
        "texto",
        "55 abc-defg"
    ]
    
    print("🧪 Testes de Padronização de Telefone:")
    print("=" * 60)
    
    for teste in testes:
        resultado = padronizar_telefone(teste, "55")  # Usando DDD 55 como padrão
        tipo = identificar_tipo_telefone(resultado) if resultado else 'inválido'
        
        print(f"Original: {str(teste):<20} → Padronizado: {str(resultado):<15} ({tipo})")

if __name__ == "__main__":
    testar_padronizacao()
    input("\nPressione Enter para sair...")
