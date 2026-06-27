# -*- coding: utf-8 -*-
"""READ-ONLY : audit complet d'isolation multi-tenant.

Detecte TOUTE requete SQL touchant une table metier sans filtre school_id,
y compris les SELECT qui possedent deja un WHERE (angle mort du scanner C2).
Fusionne les fragments de chaines adjacents pour limiter les faux positifs.
"""
import tokenize
import io
import re
import json
import mysql.connector
from db import DB_CONFIG

DBN = DB_CONFIG['database']
conn = mysql.connector.connect(**DB_CONFIG)
cur = conn.cursor()
cur.execute("SELECT DISTINCT TABLE_NAME FROM information_schema.columns "
            "WHERE table_schema=%s AND column_name='school_id'", (DBN,))
TEN = {r[0].lower() for r in cur.fetchall()}
cur.execute("SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=%s", (DBN,))
ALL_TABLES = {r[0].lower() for r in cur.fetchall()}
cur.close()
conn.close()

# Tables metier qui DEVRAIENT etre cloisonnees (mandat utilisateur)
SHOULD_BE_TENANT = {
    'users', 'courses', 'modules', 'professeur_classes', 'lectures', 'assignments',
    'assignment_submissions', 'notes', 'gradebook', 'absences', 'presences',
    'presences_generales', 'emploi_temps', 'exams', 'documents', 'notifications',
    'administrators_profiles', 'professors_profiles', 'students_profiles',
    'admin_roles', 'admin_permissions', 'admin_role_permissions',
    'demandes_attestations', 'attestations_blockchain', 'attestations_historique',
    'admissions_candidats', 'admissions_documents', 'admissions_communications',
    'admissions_entretiens', 'admissions_historique', 'admissions_paiements',
    'admissions_filiere_config', 'paiements', 'depenses',
}
# Tables volontairement globales (hors cloisonnement)
GLOBAL_OK = {'schools', 'subscription_plans', 'academic_years'}
SCHEMA_GAP = sorted((SHOULD_BE_TENANT - TEN) & ALL_TABLES)

KW = re.compile(r'\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\b', re.I)
RE_TBL = re.compile(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?', re.I)
USERBIND = re.compile(r'\b(user_id|etudiant_id|student_id|professeur_id|prof_id|candidat_id)\b', re.I)
# Liaison par cle primaire / login : non listant -> a trier manuellement (id session vs requete)
PKBIND = re.compile(r'(?:\b|\.)id\s*=\s*%s|\bemail\s*=\s*%s|\bidentifiant\s*=\s*%s', re.I)
MERGE_THROUGH = {tokenize.NL, tokenize.COMMENT}


def merged_strings(src):
    """Genere (start_line, texte_concatene) en fusionnant fragments adjacents."""
    buf, start = [], None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING:
            if start is None:
                start = tok.start[0]
            buf.append(tok.string)
        elif tok.type in MERGE_THROUGH or (tok.type == tokenize.OP and tok.string == '+'):
            continue
        else:
            if buf:
                yield start, ' '.join(buf)
            buf, start = [], None
    if buf:
        yield start, ' '.join(buf)


def classify(s):
    u = s.upper()
    if re.search(r'\bUPDATE\b', s[:60], re.I):
        t = 'UPDATE'
    elif re.search(r'\bDELETE\b', s[:60], re.I):
        t = 'DELETE'
    elif re.search(r'\bINSERT\b|\bREPLACE\b', s[:60], re.I):
        t = 'INSERT'
    else:
        t = 'SELECT'
    return t


def scan(path):
    src = open(path, encoding='utf-8').read()
    findings = []
    for line, s in merged_strings(src):
        if not KW.search(s):
            continue
        tabs = {m.group(1).lower() for m in RE_TBL.finditer(s)}
        ten_hit = tabs & TEN
        gap_hit = tabs & set(SCHEMA_GAP)
        if not ten_hit and not gap_hit:
            continue
        if 'school_id' in s.lower():
            continue
        t = classify(s)
        ub = bool(USERBIND.search(s))
        pk = bool(PKBIND.search(s))
        target = (RE_TBL.search(s).group(1).lower() if RE_TBL.search(s) else '?')
        if ten_hit:
            if t == 'INSERT':
                sev = 'INSERT'
            elif ub:
                sev = 'LOW'
            elif pk:
                sev = 'PKBIND'
            else:
                sev = 'HIGH'
        else:
            sev = 'SCHEMA'
        findings.append({'line': line, 'type': t, 'target': target, 'sev': sev,
                         'tables': sorted(ten_hit or gap_hit), 'ub': ub})
    return findings


FILES = ['app.py', 'routes/admissions.py', 'services/admissions_services.py',
         'student_enrollment_service.py', 'services/subscriptions.py', 'routes/subscriptions.py',
         'permissions.py', 'notification_services.py']
report = {}
grand = {'HIGH': 0, 'PKBIND': 0, 'LOW': 0, 'INSERT': 0, 'SCHEMA': 0}
for f in FILES:
    try:
        fnd = scan(f)
    except FileNotFoundError:
        continue
    report[f] = fnd
    by = {'HIGH': 0, 'PKBIND': 0, 'LOW': 0, 'INSERT': 0, 'SCHEMA': 0}
    for r in fnd:
        by[r['sev']] += 1
        grand[r['sev']] += 1
    print(f"\n##### {f}  HIGH={by['HIGH']} PKBIND={by['PKBIND']} LOW={by['LOW']} "
          f"INSERT={by['INSERT']} SCHEMA={by['SCHEMA']}")
    for sev in ('HIGH', 'INSERT', 'PKBIND', 'LOW', 'SCHEMA'):
        for r in [x for x in fnd if x['sev'] == sev]:
            print(f"  {sev:6} L{r['line']:5} {r['type']:6} -> {r['target']:24} {r['tables']}")

json.dump(report, open('_audit_tenant.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print("\nSCHEMA_GAP (tables sans school_id a migrer):", SCHEMA_GAP)
print("TOTAUX:", grand)
