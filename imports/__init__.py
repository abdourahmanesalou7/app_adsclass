"""
Module ADSClass Imports — Architecture ETL professionnelle

Sources externes (Excel/CSV/Word/MySQL/API)
    └─> CONNECTORS LAYER (adapters normalisés)
            └─> ETL ENGINE (parse, map, validate, transform)
                    └─> STAGING DATABASE (validation layer)
                            └─> ADSCLASS CORE DB (commit final)

Composants :
- connectors/   : adaptateurs par source
- etl/          : moteur de traitement
- schemas.py    : whitelist de tables cibles + champs autorisés
- staging.py    : gestion des lignes en attente de validation
- security.py   : contrôles sécurité fichiers/contenu
- routes.py     : Blueprint Flask /admin/imports
- db_init.py    : création des tables techniques d'import
"""
