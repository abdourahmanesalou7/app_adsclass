"""
Schémas cibles autorisés pour l'import.

Chaque schéma décrit :
- label              : nom affiché
- description        : explication
- target_tables      : tables physiques touchées (en ordre d'insertion)
- fields             : { nom_champ_logique : { type, required, max_len, choices, table, column } }
- unique_keys        : champs servant à détecter doublons (upsert)
- post_processor     : nom optionnel d'une fonction de post-traitement

Tout champ NON listé est rejeté. Whitelist stricte.
"""
from werkzeug.security import generate_password_hash


def _hash_password(value):
    if not value:
        value = "ChangeMe123!"
    return generate_password_hash(str(value))


SCHEMAS = {
    'students': {
        'label': 'Étudiants',
        'description': 'Importer des étudiants (table users + students_profiles)',
        'target_tables': ['users', 'students_profiles'],
        'unique_keys': ['email'],
        'auto_credentials': True,
        'fields': {
            'nom':              {'type': 'str',  'required': True,  'max_len': 100, 'table': 'users'},
            'prenom':           {'type': 'str',  'required': True,  'max_len': 100, 'table': 'users'},
            'email':            {'type': 'email','required': False, 'max_len': 255, 'table': 'users'},
            'password':         {'type': 'str',  'required': False, 'max_len': 255, 'table': 'users'},
            'telephone':        {'type': 'str',  'required': False, 'max_len': 20,  'table': 'users'},
            'filiere':          {'type': 'str',  'required': False, 'max_len': 100, 'table': 'users'},
            'niveau':           {'type': 'str',  'required': False, 'max_len': 50,  'table': 'users'},
            'classe':           {'type': 'str',  'required': False, 'max_len': 50,  'table': 'users'},
            'matricule':        {'type': 'str',  'required': False, 'max_len': 50,  'table': 'students_profiles'},
            'date_naissance':   {'type': 'date', 'required': False,                  'table': 'students_profiles'},
            'lieu_naissance':   {'type': 'str',  'required': False, 'max_len': 150, 'table': 'students_profiles'},
            'sexe':             {'type': 'enum', 'choices': ['M','F','Autre'], 'required': False, 'table': 'students_profiles'},
            'nationalite':      {'type': 'str',  'required': False, 'max_len': 80,  'table': 'students_profiles'},
            'adresse':          {'type': 'str',  'required': False, 'max_len': 500, 'table': 'students_profiles'},
            'ville':            {'type': 'str',  'required': False, 'max_len': 100, 'table': 'students_profiles'},
            'parent_nom':       {'type': 'str',  'required': False, 'max_len': 150, 'table': 'students_profiles'},
            'parent_telephone': {'type': 'str',  'required': False, 'max_len': 30,  'table': 'students_profiles'},
            'parent_email':     {'type': 'email','required': False, 'max_len': 150, 'table': 'students_profiles'},
            'date_inscription': {'type': 'date', 'required': False,                  'table': 'students_profiles'},
        },
        'fixed_values': {'role': 'etudiant'},
    },
    'professeurs': {
        'label': 'Professeurs',
        'description': 'Importer des professeurs (users + professors_profiles)',
        'target_tables': ['users', 'professors_profiles'],
        'unique_keys': ['email'],
        'auto_credentials': True,
        'fields': {
            'nom':           {'type': 'str',  'required': True,  'max_len': 100, 'table': 'users'},
            'prenom':        {'type': 'str',  'required': True,  'max_len': 100, 'table': 'users'},
            'email':         {'type': 'email','required': False, 'max_len': 255, 'table': 'users'},
            'password':      {'type': 'str',  'required': False, 'max_len': 255, 'table': 'users'},
            'telephone':     {'type': 'str',  'required': False, 'max_len': 20,  'table': 'users'},
            'specialite':    {'type': 'str',  'required': False, 'max_len': 100, 'table': 'users'},
            'matricule':     {'type': 'str',  'required': False, 'max_len': 50,  'table': 'professors_profiles'},
            'diplome':       {'type': 'str',  'required': False, 'max_len': 150, 'table': 'professors_profiles'},
            'grade':         {'type': 'str',  'required': False, 'max_len': 100, 'table': 'professors_profiles'},
            'departement':   {'type': 'str',  'required': False, 'max_len': 150, 'table': 'professors_profiles'},
            'date_embauche': {'type': 'date', 'required': False,                  'table': 'professors_profiles'},
            'type_contrat':  {'type': 'enum', 'choices': ['CDI','CDD','Vacataire','Stagiaire'], 'required': False, 'table': 'professors_profiles'},
            'salaire_base':  {'type': 'float','required': False,                  'table': 'professors_profiles'},
            'biographie':    {'type': 'str',  'required': False, 'max_len': 2000, 'table': 'professors_profiles'},
        },
        'fixed_values': {'role': 'professeur'},
    },
    'administrateurs': {
        'label': 'Administrateurs',
        'description': 'Importer des administrateurs (users + administrators_profiles)',
        'target_tables': ['users', 'administrators_profiles'],
        'unique_keys': ['email'],
        'auto_credentials': True,
        'fields': {
            'nom':           {'type': 'str',  'required': True,  'max_len': 100, 'table': 'users'},
            'prenom':        {'type': 'str',  'required': True,  'max_len': 100, 'table': 'users'},
            'email':         {'type': 'email','required': False, 'max_len': 255, 'table': 'users'},
            'password':      {'type': 'str',  'required': False, 'max_len': 255, 'table': 'users'},
            'telephone':     {'type': 'str',  'required': False, 'max_len': 20,  'table': 'users'},
            'matricule':     {'type': 'str',  'required': False, 'max_len': 50,  'table': 'administrators_profiles'},
            'service':       {'type': 'str',  'required': False, 'max_len': 150, 'table': 'administrators_profiles'},
            'fonction':      {'type': 'str',  'required': False, 'max_len': 150, 'table': 'administrators_profiles'},
            'date_embauche': {'type': 'date', 'required': False,                  'table': 'administrators_profiles'},
            'type_contrat':  {'type': 'enum', 'choices': ['CDI','CDD','Vacataire','Stagiaire'], 'required': False, 'table': 'administrators_profiles'},
        },
        'fixed_values': {'role': 'admin'},
    },
    'courses': {
        'label': 'Cours',
        'description': 'Importer des cours (table courses)',
        'target_tables': ['courses'],
        'unique_keys': ['nom_cours', 'start'],
        'fields': {
            'nom_cours':      {'type': 'str',     'required': True,  'max_len': 255, 'table': 'courses'},
            'professeur_id':  {'type': 'int',     'required': False,                 'table': 'courses'},
            'professeur_nom': {'type': 'str',     'required': False, 'max_len': 255, 'table': 'courses'},
            'start':          {'type': 'datetime','required': True,                  'table': 'courses'},
            'end':            {'type': 'datetime','required': True,                  'table': 'courses'},
            'filiere':        {'type': 'str',     'required': True,  'max_len': 100, 'table': 'courses'},
            'salle':          {'type': 'str',     'required': False, 'max_len': 50,  'table': 'courses'},
            'description':    {'type': 'str',     'required': False, 'max_len': 2000,'table': 'courses'},
            'jour_semaine':   {'type': 'str',     'required': False, 'max_len': 20,  'table': 'courses'},
            'heure_debut':    {'type': 'time',    'required': False,                 'table': 'courses'},
            'heure_fin':      {'type': 'time',    'required': False,                 'table': 'courses'},
        },
    },
    'paiements': {
        'label': 'Paiements',
        'description': 'Importer des paiements étudiants',
        'target_tables': ['paiements'],
        'unique_keys': [],
        'fields': {
            'etudiant_id':  {'type': 'int',   'required': True,  'table': 'paiements'},
            'date':         {'type': 'date',  'required': True,  'table': 'paiements'},
            'montant':      {'type': 'float', 'required': True,  'table': 'paiements'},
            'moyen':        {'type': 'str',   'required': False, 'max_len': 50, 'table': 'paiements'},
            'observation':  {'type': 'str',   'required': False, 'max_len': 2000, 'table': 'paiements'},
        },
    },
    'depenses': {
        'label': 'Dépenses',
        'description': 'Importer des dépenses',
        'target_tables': ['depenses'],
        'unique_keys': [],
        'fields': {
            'date':        {'type': 'date',  'required': True, 'table': 'depenses'},
            'description': {'type': 'str',   'required': True, 'max_len': 2000, 'table': 'depenses'},
            'montant':     {'type': 'float', 'required': True, 'table': 'depenses'},
        },
    },
}

TRANSFORMS = {
    'hash_password': _hash_password,
}


def get_schema(key):
    return SCHEMAS.get(key)


def list_schemas():
    return [(k, v['label'], v['description']) for k, v in SCHEMAS.items()]
