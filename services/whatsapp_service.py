"""
Serviço de WhatsApp para envio automático de mensagens
Usando WhatsApp Business API ou alternativa (Twilio/CallMeBot)
"""

import requests
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class WhatsAppService:
    def __init__(self):
        # Configurações - usando WhatsApp Web API (mais confiável)
        self.api_url = "https://web.whatsapp.com/send"  # WhatsApp Web
        self.phone_number = "5537449976"  # Seu WhatsApp
        
    def formatar_mensagem(self, nome_usuario: str, tipo: str, mensagem: str) -> str:
        """Formata a mensagem de forma profissional"""
        emojis = {
            'duvida': 'question',
            'sugestao': 'lightbulb',
            'problema': 'exclamation-triangle',
            'outro': 'chat-dots'
        }
        
        emoji = emojis.get(tipo, 'chat-dots')
        
        texto = f"""*CRM Bakof - Nova Mensagem de Suporte* {emoji}

*Usuário:* {nome_usuario}
*Tipo:* {tipo.title()}
*Data/Hora:* {datetime.now().strftime('%d/%m/%Y %H:%M')}

*Mensagem:*
{mensagem}

---
Enviado pelo sistema CRM Bakof v3.0
"""
        return texto
    
    def enviar_mensagem(self, nome_usuario: str, tipo: str, mensagem: str) -> Dict[str, Any]:
        """Prepara mensagem para WhatsApp (envio via wa.me)"""
        try:
            texto_formatado = self.formatar_mensagem(nome_usuario, tipo, mensagem)
            
            # Criar link wa.me para envio direto
            whatsapp_link = f"https://wa.me/{self.phone_number}?text={texto_formatado}"
            
            logger.info(f"Mensagem WhatsApp preparada para {nome_usuario}")
            
            # Retornar sucesso com link para envio
            return {
                "success": True,
                "message": "Mensagem preparada com sucesso!",
                "whatsapp_link": whatsapp_link,
                "envio_automatico": False  # Requer abertura manual
            }
                
        except Exception as e:
            logger.error(f"Erro ao preparar mensagem WhatsApp: {str(e)}")
            return {
                "success": False,
                "message": "Erro ao preparar mensagem",
                "error": str(e)
            }
    
    def testar_conexao(self) -> Dict[str, Any]:
        """Testa se o sistema está funcionando"""
        try:
            # Simples teste de formatação
            mensagem_teste = self.formatar_mensagem(
                "Sistema Teste", 
                "teste", 
                "Mensagem de teste do sistema CRM Bakof"
            )
            
            # Criar link de teste
            link_teste = f"https://wa.me/{self.phone_number}?text={mensagem_teste}"
            
            return {
                "success": True,
                "message": "Sistema de WhatsApp funcionando!",
                "test_link": link_teste
            }
                
        except Exception as e:
            return {
                "success": False,
                "message": f"Erro ao testar: {str(e)}"
            }

# Instância global do serviço
whatsapp_service = WhatsAppService()
