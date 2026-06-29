import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(app):
    """
    Configuration des logs AdsClass
    Compatible Windows et Linux
    """

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / "app.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=10,
        encoding="utf-8"
    )

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s | %(module)s | %(message)s"
    )

    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    # Ajouter le handler uniquement s'il n'existe pas déjà
    already_added = any(
        isinstance(h, RotatingFileHandler)
        for h in app.logger.handlers
    )

    if not already_added:
        app.logger.addHandler(file_handler)

    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False

    # Écrire le message uniquement une fois par processus
    if not getattr(app, "_logging_initialized", False):
        app.logger.info("===== AdsClass Logging initialisé =====")
        app._logging_initialized = True