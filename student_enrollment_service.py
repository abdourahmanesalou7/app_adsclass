"""
AdsClass — Inscription étudiant depuis le pipeline admissions
"""

import re
import secrets
import string
from datetime import datetime


def ensure_student_account_columns(cursor):
    """Colonnes pour identifiant, filière liée et mot de passe temporaire imprimable."""
    additions = [
        ("identifiant", "VARCHAR(50) NULL"),
        ("filiere_id", "INT NULL"),
        ("password_temp", "VARCHAR(100) NULL"),
        ("must_change_password", "TINYINT(1) DEFAULT 0"),
    ]
    for col, definition in additions:
        cursor.execute(f"SHOW COLUMNS FROM users LIKE '{col}'")
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
    try:
        cursor.execute("SHOW INDEX FROM users WHERE Key_name = 'idx_users_identifiant'")
        if not cursor.fetchone():
            cursor.execute("CREATE UNIQUE INDEX idx_users_identifiant ON users (identifiant)")
    except Exception:
        pass


NIVEAU_SHORT_TO_LONG = {
    'L1': 'Licence 1', 'L2': 'Licence 2', 'L3': 'Licence 3',
    'M1': 'Master 1', 'M2': 'Master 2',
}
NIVEAU_LONG_TO_SHORT = {v: k for k, v in NIVEAU_SHORT_TO_LONG.items()}


def _slug(text):
    return re.sub(r'[^a-z0-9]', '', (text or '').lower())


def normaliser_niveau(niveau, fallback='L1'):
    if not niveau:
        return fallback
    n = str(niveau).strip().upper()
    for abbr in ('L1', 'L2', 'L3', 'M1', 'M2'):
        if n == abbr or n.startswith(abbr + ' ') or f' {abbr}' in n:
            return abbr
    if 'LICENCE 1' in n or n == 'LICENCE1':
        return 'L1'
    if 'LICENCE 2' in n:
        return 'L2'
    if 'LICENCE 3' in n:
        return 'L3'
    if 'MASTER 1' in n or n == 'MASTER1':
        return 'M1'
    if 'MASTER 2' in n:
        return 'M2'
    return n.split()[0][:10] if n else fallback


def niveau_canonique(niveau, fallback='Licence 1'):
    """Forme longue canonique du niveau ('Master 2', 'Licence 1', ...)."""
    short = normaliser_niveau(niveau, fallback='')
    if short in NIVEAU_SHORT_TO_LONG:
        return NIVEAU_SHORT_TO_LONG[short]
    return (str(niveau).strip() if niveau else '') or fallback


def niveau_aliases(niveau):
    """Toutes les écritures équivalentes pour matcher un niveau (SQL tolérant)."""
    if not niveau:
        return []
    aliases = {str(niveau).strip()}
    short = normaliser_niveau(niveau, fallback='')
    if short:
        aliases.add(short)
        long_form = NIVEAU_SHORT_TO_LONG.get(short)
        if long_form:
            aliases.add(long_form)
    return [a for a in aliases if a]


def filiere_aliases(cursor, filiere_name):
    """Toutes les écritures équivalentes pour matcher une filière (nom canonique + code)."""
    if not filiere_name:
        return []
    aliases = {str(filiere_name).strip()}
    try:
        row = resolve_filiere_by_name(cursor, filiere_name)
    except Exception:
        row = None
    if row:
        if row.get('nom'):
            aliases.add(row['nom'].strip())
        if row.get('code'):
            aliases.add(row['code'].strip())
    return [a for a in aliases if a]


def build_filiere_niveau_where(cursor, filiere_name, niveau,
                                course_alias='c', accept_null_niveau=True):
    """(clause SQL, params) pour matcher filière + niveau de manière tolérante."""
    f_aliases = filiere_aliases(cursor, filiere_name)
    n_aliases = niveau_aliases(niveau)
    clauses, params = [], []
    if f_aliases:
        clauses.append(f"{course_alias}.filiere IN ({','.join(['%s'] * len(f_aliases))})")
        params.extend(f_aliases)
    if n_aliases:
        niveau_clause = f"{course_alias}.niveau IN ({','.join(['%s'] * len(n_aliases))})"
        if accept_null_niveau:
            niveau_clause = (
                f"({niveau_clause} OR {course_alias}.niveau IS NULL "
                f"OR {course_alias}.niveau = '')"
            )
        clauses.append(niveau_clause)
        params.extend(n_aliases)
    return (" AND ".join(clauses) if clauses else "1=1"), params


def resolve_tenant_school_id(school_id=None):
    """Retourne un school_id entier ; lève ValueError si impossible à résoudre."""
    if school_id is not None:
        sid = int(school_id)
        if sid >= 1:
            return sid
    try:
        import tenant as tenant_mod
        if tenant_mod.has_request_context():
            sid = tenant_mod.current_school_id()
            if sid and int(sid) >= 1:
                return int(sid)
    except Exception:
        pass
    raise ValueError("school_id requis pour l'isolation multi-tenant")


def student_enrollment_join_sql(c_alias='c', et_alias='et'):
    """JOIN tenant-safe entre courses et emploi_temps (course_id + school_id)."""
    return (
        f"JOIN emploi_temps {et_alias} ON {c_alias}.id = {et_alias}.course_id "
        f"AND ({et_alias}.school_id = {c_alias}.school_id OR {et_alias}.school_id IS NULL)"
    )


def student_course_tenant_where(c_alias='c', et_alias='et', school_id=None):
    """Fragment WHERE + params pour filtrer cours/inscriptions par école."""
    sid = resolve_tenant_school_id(school_id)
    return (
        f" AND {c_alias}.school_id = %s "
        f"AND ({et_alias}.school_id = %s OR {et_alias}.school_id IS NULL)",
        (sid, sid),
    )


def generer_nom_classe(niveau, filiere_nom, filiere_code=None):
    niveau_abbrev = normaliser_niveau(niveau, fallback='L1')
    code = (filiere_code or filiere_nom or 'GEN').upper().replace(' ', '')[:12]
    return f"{niveau_abbrev}-{code}"


def generer_identifiant(cursor, filiere_code, niveau):
    year = datetime.now().year
    code = re.sub(r'[^A-Z0-9]', '', (filiere_code or 'ADS').upper())[:8] or 'ADS'
    niv = normaliser_niveau(niveau)
    prefix = f"{code}-{niv}-{year}-"
    cursor.execute(
        "SELECT identifiant FROM users WHERE identifiant LIKE %s ORDER BY identifiant DESC LIMIT 1",
        (prefix + '%',),
    )
    row = cursor.fetchone()
    seq = 1
    if row and row.get('identifiant'):
        try:
            seq = int(str(row['identifiant']).split('-')[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


def generer_mot_de_passe(length=10):
    rng = secrets.SystemRandom()
    chars = [
        rng.choice(string.ascii_uppercase),
        rng.choice(string.ascii_lowercase),
        rng.choice(string.digits),
    ]
    alphabet = string.ascii_letters + string.digits
    chars += [rng.choice(alphabet) for _ in range(max(4, length) - 3)]
    rng.shuffle(chars)
    return ''.join(chars)


def resolve_filiere_from_candidat(cursor, candidat, school_id=None):
    if school_id is None:
        school_id = candidat.get('school_id')
    sch_clause = " AND school_id = %s" if school_id is not None else ""
    sch_param = (school_id,) if school_id is not None else ()
    if candidat.get('filiere_id'):
        cursor.execute(
            "SELECT * FROM filieres WHERE id = %s AND est_active = 1" + sch_clause,
            (candidat['filiere_id'], *sch_param),
        )
        row = cursor.fetchone()
        if row:
            return row

    for key in ('filiere_nom', 'programme_souhaite'):
        nom = (candidat.get(key) or '').strip()
        if not nom:
            continue
        cursor.execute(
            "SELECT * FROM filieres WHERE nom = %s AND est_active = 1" + sch_clause,
            (nom, *sch_param),
        )
        row = cursor.fetchone()
        if row:
            return row
        cursor.execute(
            "SELECT * FROM filieres WHERE code = %s AND est_active = 1" + sch_clause,
            (nom.upper(), *sch_param),
        )
        row = cursor.fetchone()
        if row:
            return row
    return None


def generer_email_etudiant(cursor, prenom, nom, suffix_id):
    base = f"{_slug(prenom)}.{_slug(nom)}".strip('.')
    if not base:
        base = f"etudiant{suffix_id}"
    email = f"{base}@adsclass.ne"
    cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
    if cursor.fetchone():
        email = f"{base}.{suffix_id}@adsclass.ne"
    return email


def _enroll_in_courses(cursor, etudiant_id, filiere, niveau, school_id=None):
    try:
        sid = resolve_tenant_school_id(school_id)
    except ValueError:
        return
    cursor.execute("SHOW COLUMNS FROM courses LIKE 'niveau'")
    has_niveau = cursor.fetchone() is not None
    f_aliases = filiere_aliases(cursor, filiere) or ([filiere] if filiere else [])
    if not f_aliases:
        return
    f_ph = ','.join(['%s'] * len(f_aliases))
    sch_clause = " AND school_id = %s"
    sch_param = (sid,)
    if has_niveau:
        n_aliases = niveau_aliases(niveau)
        if n_aliases:
            n_ph = ','.join(['%s'] * len(n_aliases))
            sql = (
                f"SELECT id FROM courses WHERE filiere IN ({f_ph}) "
                f"AND (niveau IN ({n_ph}) OR niveau IS NULL OR niveau = ''){sch_clause}"
            )
            cursor.execute(sql, (*f_aliases, *n_aliases, *sch_param))
        else:
            cursor.execute(f"SELECT id FROM courses WHERE filiere IN ({f_ph}){sch_clause}",
                           (*f_aliases, *sch_param))
    else:
        cursor.execute(f"SELECT id FROM courses WHERE filiere IN ({f_ph}){sch_clause}",
                       (*f_aliases, *sch_param))
    for row in cursor.fetchall():
        try:
            cursor.execute(
                """
                INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications, school_id)
                VALUES (%s, %s, 'etudiant', 1, 1, %s)
                """,
                (etudiant_id, row['id'], sid),
            )
        except Exception:
            pass


def _row_get(row, key, index):
    """Lecture tolérante d'une ligne curseur (dict ou tuple)."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def sync_enrollments_for_student(cursor, student_id, school_id=None):
    """Événement métier : (ré)inscrit un étudiant à TOUS les cours correspondant
    à son école / filière / niveau. Idempotent (INSERT IGNORE). À appeler après
    création d'un étudiant ou modification de sa filière / niveau / classe.
    Requiert un curseur dictionnaire. Retourne 1 si l'étudiant a été traité."""
    cursor.execute(
        "SELECT filiere, niveau, school_id FROM users WHERE id = %s AND role = 'etudiant'",
        (student_id,),
    )
    row = cursor.fetchone()
    if not row:
        return 0
    filiere = _row_get(row, 'filiere', 0)
    niveau = _row_get(row, 'niveau', 1)
    sid = school_id if school_id is not None else _row_get(row, 'school_id', 2)
    _enroll_in_courses(cursor, student_id, filiere, niveau, sid)
    return 1


def sync_enrollments_for_course(cursor, course_id, school_id=None):
    """Événement métier : inscrit TOUS les étudiants correspondant (même école /
    filière / niveau) au cours donné. Idempotent (INSERT IGNORE). À appeler après
    création d'un cours. Requiert un curseur dictionnaire. Retourne le nombre de
    nouvelles inscriptions."""
    cursor.execute(
        "SELECT filiere, niveau, school_id FROM courses WHERE id = %s", (course_id,)
    )
    c = cursor.fetchone()
    if not c:
        return 0
    filiere = _row_get(c, 'filiere', 0)
    niveau = _row_get(c, 'niveau', 1)
    sid_raw = school_id if school_id is not None else _row_get(c, 'school_id', 2)
    try:
        sid = resolve_tenant_school_id(sid_raw)
    except ValueError:
        return 0
    f_aliases = filiere_aliases(cursor, filiere) or ([filiere] if filiere else [])
    if not f_aliases:
        return 0
    f_ph = ','.join(['%s'] * len(f_aliases))
    # Matcher aussi les étudiants par filiere_id (cas où users.filiere texte
    # diffère du nom canonique mais filiere_id pointe bien sur la filière).
    try:
        f_row = resolve_filiere_by_name(cursor, filiere, sid)
        filiere_id = f_row['id'] if f_row else None
    except Exception:
        filiere_id = None
    fid_clause = " OR filiere_id = %s" if filiere_id is not None else ""
    fid_param = (filiere_id,) if filiere_id is not None else ()
    sch_clause = " AND school_id = %s"
    sch_param = (sid,)
    n_aliases = niveau_aliases(niveau)
    if n_aliases:
        n_ph = ','.join(['%s'] * len(n_aliases))
        sql = (
            "INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications, school_id) "
            "SELECT id, %s, 'etudiant', 1, 1, %s FROM users "
            f"WHERE role = 'etudiant' AND (filiere IN ({f_ph}){fid_clause}) "
            f"AND niveau IN ({n_ph}){sch_clause}"
        )
        cursor.execute(sql, (course_id, sid, *f_aliases, *fid_param, *n_aliases, *sch_param))
    else:
        sql = (
            "INSERT IGNORE INTO emploi_temps (user_id, course_id, role, visible, notifications, school_id) "
            "SELECT id, %s, 'etudiant', 1, 1, %s FROM users "
            f"WHERE role = 'etudiant' AND (filiere IN ({f_ph}){fid_clause}){sch_clause}"
        )
        cursor.execute(sql, (course_id, sid, *f_aliases, *fid_param, *sch_param))
    return cursor.rowcount or 0


def remove_course_enrollments(cursor, course_id, school_id=None):
    """Événement métier : retire les inscriptions emploi_temps d'un cours
    supprimé ou archivé (scopé école si fournie). Retourne le nb de lignes."""
    if school_id is not None:
        cursor.execute(
            "DELETE FROM emploi_temps WHERE course_id = %s AND school_id = %s",
            (course_id, school_id),
        )
    else:
        cursor.execute("DELETE FROM emploi_temps WHERE course_id = %s", (course_id,))
    return cursor.rowcount or 0


def enrollir_candidat_en_etudiant(conn, candidat, generate_password_hash, candidat_id=None):
    """
    Crée le compte étudiant, lie le candidat, inscrit aux cours existants.
    Retourne un dict avec identifiants imprimables.
    """
    if not candidat:
        raise ValueError("Candidat introuvable")
    if candidat.get('etudiant_id'):
        raise ValueError("Ce candidat est déjà inscrit comme étudiant")

    cursor = conn.cursor(dictionary=True)
    ensure_student_account_columns(cursor)

    filiere_row = resolve_filiere_from_candidat(cursor, candidat)
    if not filiere_row:
        raise ValueError(
            "Filière introuvable ou inactive. Sélectionnez une filière valide "
            "dans Gestion des filières & modules."
        )

    cid = candidat_id or candidat['id']
    filiere_nom = filiere_row['nom']
    filiere_code = filiere_row.get('code') or filiere_nom[:6].upper().replace(' ', '')
    niveau_src = candidat.get('niveau') or filiere_row.get('niveau')
    niveau_short = normaliser_niveau(niveau_src)
    niveau = NIVEAU_SHORT_TO_LONG.get(niveau_short, niveau_short)
    classe = generer_nom_classe(niveau_short, filiere_nom, filiere_code)
    identifiant = generer_identifiant(cursor, filiere_code, niveau_short)
    password_plain = generer_mot_de_passe()
    password_hash = generate_password_hash(password_plain)

    email = (candidat.get('email') or '').strip().lower()
    if not email.endswith('@adsclass.ne'):
        email = generer_email_etudiant(cursor, candidat['prenom'], candidat['nom'], cid)
    else:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            email = generer_email_etudiant(cursor, candidat['prenom'], candidat['nom'], cid)

    telephone = candidat.get('telephone')

    cursor.execute(
        """
        INSERT INTO users (
            nom, prenom, email, password, role, filiere, niveau, classe,
            identifiant, filiere_id, password_temp, must_change_password, telephone, school_id
        ) VALUES (%s, %s, %s, %s, 'etudiant', %s, %s, %s, %s, %s, %s, 1, %s, %s)
        """,
        (
            candidat['nom'], candidat['prenom'], email, password_hash,
            filiere_nom, niveau, classe, identifiant, filiere_row['id'],
            password_plain, telephone, candidat.get('school_id'),
        ),
    )
    etudiant_id = cursor.lastrowid
    _enroll_in_courses(cursor, etudiant_id, filiere_nom, niveau, candidat.get('school_id'))

    cursor.execute(
        "UPDATE admissions_candidats SET etudiant_id=%s, statut='inscrit', filiere_id=%s WHERE id=%s AND school_id=%s",
        (etudiant_id, filiere_row['id'], cid, candidat.get('school_id')),
    )

    return {
        'etudiant_id': etudiant_id,
        'identifiant': identifiant,
        'email': email,
        'password_plain': password_plain,
        'filiere': filiere_nom,
        'filiere_id': filiere_row['id'],
        'niveau': niveau,
        'classe': classe,
        'prenom': candidat['prenom'],
        'nom': candidat['nom'],
        'candidat_id': cid,
    }


def get_active_filieres(cursor, school_id=None):
    if school_id is not None:
        cursor.execute(
            "SELECT id, nom, code, niveau FROM filieres "
            "WHERE est_active = 1 AND school_id = %s ORDER BY nom ASC",
            (school_id,),
        )
    else:
        cursor.execute(
            "SELECT id, nom, code, niveau FROM filieres WHERE est_active = 1 ORDER BY nom ASC"
        )
    return cursor.fetchall()


def get_canonical_active_filieres(cursor, school_id=None):
    """Filières actives sans doublon (ex. « IA » + « Intelligence Artificielle » même code)."""
    raw = get_active_filieres(cursor, school_id)
    by_code = {}
    without_code = []

    for f in raw:
        code = (f.get('code') or '').strip().upper()
        if code:
            if code not in by_code or len(f.get('nom') or '') > len(by_code[code].get('nom') or ''):
                by_code[code] = f
        else:
            without_code.append(f)

    codes_taken = set(by_code.keys())
    result = list(by_code.values())

    for f in without_code:
        nom_compact = re.sub(r'[^a-z0-9]', '', (f.get('nom') or '').lower())
        # Ignorer si le nom est déjà couvert par un code existant (ex. nom « IA », code « IA » ailleurs)
        if nom_compact.upper() in codes_taken or nom_compact in codes_taken:
            continue
        dup = any(
            re.sub(r'[^a-z0-9]', '', (x.get('nom') or '').lower()) == nom_compact
            for x in result
        )
        if not dup:
            result.append(f)

    return sorted(result, key=lambda x: (x.get('nom') or '').lower())


def _match_filiere_id_for_student(student, filieres_index):
    """Associe un étudiant à une filière canonique (id)."""
    if student.get('filiere_id'):
        fid = student['filiere_id']
        if fid in filieres_index['by_id']:
            return fid

    text = (student.get('filiere') or '').strip().lower()
    if not text:
        return None

    if text in filieres_index['by_nom']:
        return filieres_index['by_nom'][text]

    text_compact = re.sub(r'[^a-z0-9]', '', text)
    if text_compact in filieres_index['by_nom_compact']:
        return filieres_index['by_nom_compact'][text_compact]

    if text in filieres_index['by_code']:
        return filieres_index['by_code'][text]

    # Alias courants (anciennes données)
    aliases = {
        'ia': 'ia',
        'intelligenceartificielle': 'ia',
        'datascience': 'ds',
        'datasci': 'ds',
        'developpementweb': 'dw',
        'devweb': 'dw',
    }
    alias_code = aliases.get(text_compact)
    if alias_code and alias_code in filieres_index['by_code']:
        return filieres_index['by_code'][alias_code]

    return None


def _build_filieres_index(filieres):
    by_id = {f['id']: f for f in filieres}
    by_nom = {}
    by_nom_compact = {}
    by_code = {}
    for f in filieres:
        nom_l = (f.get('nom') or '').strip().lower()
        if nom_l:
            by_nom[nom_l] = f['id']
            by_nom_compact[re.sub(r'[^a-z0-9]', '', nom_l)] = f['id']
        code_l = (f.get('code') or '').strip().lower()
        if code_l:
            by_code[code_l] = f['id']
    return {'by_id': by_id, 'by_nom': by_nom, 'by_nom_compact': by_nom_compact, 'by_code': by_code}


def sync_legacy_student_filieres(cursor, filieres=None, school_id=None):
    """Aligne users.filiere / filiere_id sur les filières canoniques."""
    filieres = filieres or get_canonical_active_filieres(cursor, school_id)
    idx = _build_filieres_index(filieres)
    if school_id is not None:
        cursor.execute(
            "SELECT id, filiere, filiere_id, niveau FROM users "
            "WHERE role = 'etudiant' AND school_id = %s", (school_id,)
        )
    else:
        cursor.execute(
            "SELECT id, filiere, filiere_id, niveau FROM users WHERE role = 'etudiant'"
        )
    for u in cursor.fetchall():
        fid = _match_filiere_id_for_student(u, idx)
        if not fid:
            continue
        canon = idx['by_id'][fid]
        niv = normaliser_niveau(u.get('niveau'))
        classe = generer_nom_classe(niv, canon['nom'], canon.get('code'))
        if u.get('filiere_id') != fid or u.get('filiere') != canon['nom']:
            cursor.execute(
                "UPDATE users SET filiere_id = %s, filiere = %s, niveau = %s, classe = %s WHERE id = %s",
                (fid, canon['nom'], niv, classe, u['id']),
            )
            # Événement métier : la filière/niveau/classe a changé → re-synchro
            # des inscriptions de cet étudiant (scopé école).
            try:
                sync_enrollments_for_student(cursor, u['id'], school_id)
            except Exception:
                pass


def count_students_by_filiere_niveau(cursor, filieres=None, school_id=None):
    filieres = filieres or get_canonical_active_filieres(cursor, school_id)
    idx = _build_filieres_index(filieres)
    counts = {}
    if school_id is not None:
        cursor.execute(
            "SELECT id, filiere, filiere_id, niveau FROM users "
            "WHERE role = 'etudiant' AND school_id = %s", (school_id,)
        )
    else:
        cursor.execute(
            "SELECT id, filiere, filiere_id, niveau FROM users WHERE role = 'etudiant'"
        )
    for u in cursor.fetchall():
        fid = _match_filiere_id_for_student(u, idx)
        if not fid:
            continue
        niv = normaliser_niveau(u.get('niveau'))
        key = (fid, niv)
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_classes_par_filiere(cursor, school_id=None):
    """Grille classes : uniquement filières actives canoniques (table filieres)."""
    ensure_student_account_columns(cursor)
    filieres = get_canonical_active_filieres(cursor, school_id)
    sync_legacy_student_filieres(cursor, filieres, school_id)
    counts = count_students_by_filiere_niveau(cursor, filieres, school_id)

    niveaux_standards = ['L1', 'L2', 'L3', 'M1', 'M2']
    classes_par_filiere = {}
    for f in filieres:
        nom = f['nom']
        code = f.get('code')
        classes_par_filiere[nom] = []
        for niv in niveaux_standards:
            classes_par_filiere[nom].append({
                'nom_classe': generer_nom_classe(niv, nom, code),
                'niveau': niv,
                'count': counts.get((f['id'], niv), 0),
                'filiere_id': f['id'],
            })
    return classes_par_filiere, filieres


def resolve_filiere_by_name(cursor, filiere_name, school_id=None):
    """Retourne la filière canonique active à partir du nom URL."""
    filieres = get_canonical_active_filieres(cursor, school_id)
    name_l = (filiere_name or '').strip().lower()
    for f in filieres:
        if (f.get('nom') or '').strip().lower() == name_l:
            return f
    idx = _build_filieres_index(filieres)
    fake = {'filiere': filiere_name, 'filiere_id': None}
    fid = _match_filiere_id_for_student(fake, idx)
    return idx['by_id'].get(fid) if fid else None


def get_students_for_class(cursor, filiere_nom, niveau, nom_classe=None, school_id=None):
    """Étudiants d'une classe — filière canonique uniquement."""
    filiere_row = resolve_filiere_by_name(cursor, filiere_nom, school_id)
    if not filiere_row:
        return [], None, nom_classe or ''

    filieres = get_canonical_active_filieres(cursor, school_id)
    sync_legacy_student_filieres(cursor, filieres, school_id)
    niv = normaliser_niveau(niveau)
    nom_classe = nom_classe or generer_nom_classe(niv, filiere_row['nom'], filiere_row.get('code'))

    cursor.execute("SHOW COLUMNS FROM users LIKE 'classe'")
    has_classe = cursor.fetchone() is not None

    base_cols = "id, prenom, nom, email, telephone, filiere, niveau, identifiant, password_temp, must_change_password"
    if has_classe:
        base_cols += ", classe"

    n_aliases = niveau_aliases(niveau) or [niv]
    n_ph = ','.join(['%s'] * len(n_aliases))
    sch_clause = " AND school_id = %s" if school_id is not None else ""
    sch_param = (school_id,) if school_id is not None else ()
    cursor.execute(
        f"""
        SELECT {base_cols}
        FROM users
        WHERE role = 'etudiant'
          AND filiere_id = %s
          AND niveau IN ({n_ph}){sch_clause}
        ORDER BY nom, prenom ASC
        """,
        (filiere_row['id'], *n_aliases, *sch_param),
    )
    students = cursor.fetchall()

    if not students:
        f_aliases = filiere_aliases(cursor, filiere_row['nom']) or [filiere_row['nom']]
        f_ph = ','.join(['%s'] * len(f_aliases))
        cursor.execute(
            f"""
            SELECT {base_cols}
            FROM users
            WHERE role = 'etudiant'
              AND filiere IN ({f_ph})
              AND niveau IN ({n_ph}){sch_clause}
            ORDER BY nom, prenom ASC
            """,
            (*f_aliases, *n_aliases, *sch_param),
        )
        students = cursor.fetchall()

    return students, filiere_row, nom_classe


def migrate_legacy_courses_filiere_niveau(cursor):
    """Réécrit les anciennes valeurs (code court / niveau abrégé) vers la forme canonique."""
    filieres = get_canonical_active_filieres(cursor)
    if not filieres:
        return 0
    code_to_nom = {}
    nom_compact_to_nom = {}
    for f in filieres:
        nom = (f.get('nom') or '').strip()
        code = (f.get('code') or '').strip()
        if not nom:
            continue
        if code:
            code_to_nom[code.upper()] = nom
        nom_compact_to_nom[re.sub(r'[^a-z0-9]', '', nom.lower())] = nom

    cursor.execute("SHOW COLUMNS FROM courses LIKE 'niveau'")
    has_niveau = cursor.fetchone() is not None
    cols = "id, filiere" + (", niveau" if has_niveau else "")
    cursor.execute(f"SELECT {cols} FROM courses")
    rows = cursor.fetchall()
    updated = 0
    for row in rows:
        current_filiere = (row.get('filiere') or '').strip()
        new_filiere = current_filiere
        if current_filiere:
            key_upper = current_filiere.upper()
            if key_upper in code_to_nom:
                new_filiere = code_to_nom[key_upper]
            else:
                compact = re.sub(r'[^a-z0-9]', '', current_filiere.lower())
                if compact in nom_compact_to_nom:
                    new_filiere = nom_compact_to_nom[compact]
                elif compact.upper() in code_to_nom:
                    new_filiere = code_to_nom[compact.upper()]

        new_niveau = None
        current_niveau = (row.get('niveau') or '').strip() if has_niveau else ''
        if has_niveau and current_niveau:
            short = normaliser_niveau(current_niveau, fallback='')
            if short in NIVEAU_SHORT_TO_LONG:
                new_niveau = NIVEAU_SHORT_TO_LONG[short]
            else:
                new_niveau = current_niveau

        changed_filiere = new_filiere != current_filiere
        changed_niveau = has_niveau and new_niveau is not None and new_niveau != current_niveau
        if changed_filiere and changed_niveau:
            cursor.execute(
                "UPDATE courses SET filiere = %s, niveau = %s WHERE id = %s",
                (new_filiere, new_niveau, row['id']),
            )
            updated += 1
        elif changed_filiere:
            cursor.execute("UPDATE courses SET filiere = %s WHERE id = %s", (new_filiere, row['id']))
            updated += 1
        elif changed_niveau:
            cursor.execute("UPDATE courses SET niveau = %s WHERE id = %s", (new_niveau, row['id']))
            updated += 1
    return updated


def migrate_legacy_users_niveau(cursor):
    """Convertit users.niveau (étudiants) en forme longue : 'M2' -> 'Master 2'."""
    cursor.execute("SELECT id, niveau FROM users WHERE role = 'etudiant'")
    rows = cursor.fetchall()
    updated = 0
    for row in rows:
        current = (row.get('niveau') or '').strip()
        if not current:
            continue
        short = normaliser_niveau(current, fallback='')
        long_form = NIVEAU_SHORT_TO_LONG.get(short)
        if long_form and long_form != current:
            cursor.execute("UPDATE users SET niveau = %s WHERE id = %s", (long_form, row['id']))
            updated += 1
    return updated


def standardize_filieres_niveaux(conn):
    """Backfill canonique : filières & niveaux uniformisés dans users et courses."""
    if conn is None:
        return
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_student_account_columns(cursor)
        sync_legacy_student_filieres(cursor)
        nb_users = migrate_legacy_users_niveau(cursor)
        nb_courses = migrate_legacy_courses_filiere_niveau(cursor)
        conn.commit()
        if nb_users or nb_courses:
            print(f"Standardisation filières/niveaux : {nb_users} étudiants, {nb_courses} cours mis à jour.")
    except Exception as e:
        print(f"Standardisation filières/niveaux ignorée : {e}")
    finally:
        cursor.close()
