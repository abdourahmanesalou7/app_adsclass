# -*- coding: utf-8 -*-
"""Point d'entrée WSGI pour serveurs de production (P1.3).

Expose l'application Flask sous les noms `app` et `application`
(certaines plateformes attendent l'un ou l'autre).

Lancement :
  - Windows / XAMPP (dev & prod légère) :
      waitress-serve --listen=0.0.0.0:8000 wsgi:app
  - Linux (production) :
      gunicorn --bind 0.0.0.0:8000 --workers 3 wsgi:app

Rappel : définir SECRET_KEY et FLASK_DEBUG=False dans .env avant tout
déploiement en production.
"""
from app import app

# Alias standard attendu par certains serveurs WSGI.
application = app


if __name__ == '__main__':
    application.run()
