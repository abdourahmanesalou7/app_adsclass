"""
Mapping intelligent : devine quelles colonnes source correspondent à quels
champs cible, par normalisation + alias + similarité.
"""
import re
import unicodedata
from difflib import SequenceMatcher

# Alias FR/EN courants → champ logique
ALIASES = {
    'nom':              ['nom', 'name', 'lastname', 'last_name', 'nom_famille', 'surname'],
    'prenom':           ['prenom', 'prénom', 'firstname', 'first_name', 'given_name'],
    'email':            ['email', 'mail', 'courriel', 'e-mail', 'adresse_email'],
    'telephone':        ['telephone', 'téléphone', 'tel', 'phone', 'mobile', 'gsm', 'tel_mobile'],
    'password':         ['password', 'motdepasse', 'mot_de_passe', 'mdp', 'pwd'],
    'filiere':          ['filiere', 'filière', 'specialite_etudes', 'cursus', 'programme', 'major'],
    'niveau':           ['niveau', 'level', 'annee', 'année', 'grade_niveau', 'classe_niveau'],
    'classe':           ['classe', 'class', 'groupe', 'group'],
    'matricule':        ['matricule', 'numero', 'numéro', 'id_etudiant', 'student_id', 'registration_number'],
    'date_naissance':   ['date_naissance', 'datenaissance', 'naissance', 'birthdate', 'date_of_birth', 'dob', 'ddn'],
    'lieu_naissance':   ['lieu_naissance', 'lieunaissance', 'lieu_de_naissance', 'birthplace', 'place_of_birth'],
    'sexe':             ['sexe', 'genre', 'gender', 'sex'],
    'nationalite':      ['nationalite', 'nationalité', 'nationality', 'pays'],
    'adresse':          ['adresse', 'address', 'domicile'],
    'ville':            ['ville', 'city'],
    'parent_nom':       ['parent_nom', 'nom_parent', 'tuteur', 'parent', 'pere', 'mere', 'father', 'mother'],
    'parent_telephone': ['parent_telephone', 'tel_parent', 'parent_phone', 'tuteur_telephone'],
    'parent_email':     ['parent_email', 'email_parent', 'parent_mail'],
    'date_inscription': ['date_inscription', 'inscription', 'date_inscription_etudiant'],
    'specialite':       ['specialite', 'spécialité', 'specialty', 'subject', 'matiere'],
    'diplome':          ['diplome', 'diplôme', 'degree', 'diploma'],
    'grade':            ['grade', 'rank', 'echelon'],
    'departement':      ['departement', 'département', 'department', 'unite', 'unité'],
    'date_embauche':    ['date_embauche', 'embauche', 'hire_date', 'date_recrutement'],
    'type_contrat':     ['type_contrat', 'contrat', 'contract_type'],
    'salaire_base':     ['salaire_base', 'salaire', 'salary', 'remuneration'],
    'biographie':       ['biographie', 'bio', 'about', 'description'],
    'service':          ['service', 'departement_admin', 'unite_admin'],
    'fonction':         ['fonction', 'poste', 'role_fonction', 'job', 'position'],
    'nom_cours':        ['nom_cours', 'cours', 'course', 'matiere', 'matière', 'subject', 'title', 'titre'],
    'professeur_id':    ['professeur_id', 'prof_id', 'teacher_id'],
    'professeur_nom':   ['professeur_nom', 'professeur', 'prof', 'teacher', 'enseignant'],
    'start':            ['start', 'debut', 'début', 'date_debut', 'start_date', 'date_heure_debut'],
    'end':              ['end', 'fin', 'date_fin', 'end_date', 'date_heure_fin'],
    'salle':            ['salle', 'room', 'local', 'amphi'],
    'description':      ['description', 'desc', 'detail', 'commentaire', 'notes', 'libelle'],
    'jour_semaine':     ['jour_semaine', 'jour', 'day', 'weekday'],
    'heure_debut':      ['heure_debut', 'h_debut', 'time_start'],
    'heure_fin':        ['heure_fin', 'h_fin', 'time_end'],
    'etudiant_id':      ['etudiant_id', 'student_id', 'id_etudiant'],
    'date':             ['date', 'date_paiement', 'date_depense'],
    'montant':          ['montant', 'amount', 'somme', 'prix', 'total'],
    'moyen':            ['moyen', 'mode_paiement', 'method', 'payment_method'],
    'observation':      ['observation', 'remarque', 'note', 'commentaire'],
}


def _normalize(s):
    if s is None:
        return ''
    s = str(s).strip().lower()
    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII')
    s = re.sub(r'[^a-z0-9]+', '_', s).strip('_')
    return s


def _best_match(src_normalized, candidates):
    best = (None, 0.0)
    for cand in candidates:
        score = SequenceMatcher(None, src_normalized, _normalize(cand)).ratio()
        if score > best[1]:
            best = (cand, score)
    return best


def suggest_mapping(source_headers, schema):
    """
    Pour chaque source_header, propose le meilleur target_field du schema.
    Retourne : { source_header : target_field_or_None }
    """
    fields = list(schema['fields'].keys())
    # Index inversé alias → champ logique
    alias_to_field = {}
    for field in fields:
        for alias in ALIASES.get(field, [field]):
            alias_to_field[_normalize(alias)] = field
        alias_to_field[_normalize(field)] = field

    mapping = {}
    used = set()
    for src in source_headers:
        src_n = _normalize(src)
        # 1. match exact alias
        if src_n in alias_to_field and alias_to_field[src_n] not in used:
            mapping[src] = alias_to_field[src_n]
            used.add(alias_to_field[src_n])
            continue
        # 2. fuzzy contre tous les alias
        best_field, best_score = None, 0.0
        for alias_n, field in alias_to_field.items():
            if field in used:
                continue
            score = SequenceMatcher(None, src_n, alias_n).ratio()
            if score > best_score:
                best_score, best_field = score, field
        if best_score >= 0.78 and best_field:
            mapping[src] = best_field
            used.add(best_field)
        else:
            mapping[src] = None
    return mapping


def apply_mapping(raw_row, mapping, schema):
    """
    Transforme un dict raw {source_col: val} en dict mapped {target_field: val}
    + applique les fixed_values du schema.
    """
    mapped = {}
    for src, tgt in mapping.items():
        if not tgt:
            continue
        mapped[tgt] = raw_row.get(src)
    for k, v in (schema.get('fixed_values') or {}).items():
        mapped.setdefault(k, v)
    return mapped
