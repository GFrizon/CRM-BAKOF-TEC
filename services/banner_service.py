from datetime import datetime

from sqlalchemy import or_

from core.models import Banner


def get_banners_ativos():
    agora = datetime.now()
    return (
        Banner.query.filter(Banner.ativo == True)
        .filter(or_(Banner.data_expiracao == None, Banner.data_expiracao >= agora))
        .order_by(Banner.data_criacao.desc())
        .all()
    )
