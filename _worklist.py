# -*- coding: utf-8 -*-
"""READ-ONLY : genere la liste de travail C1/C2/C3 (lignes precises)."""
import tokenize, io, re, json
import mysql.connector
from db import DB_CONFIG

DBN = DB_CONFIG['database']
conn = mysql.connector.connect(**DB_CONFIG); cur = conn.cursor()
cur.execute("SELECT DISTINCT TABLE_NAME FROM information_schema.columns "
            "WHERE table_schema=%s AND column_name='school_id'", (DBN,))
TEN = {r[0].lower() for r in cur.fetchall()}
cur.close(); conn.close()

KW = re.compile(r'\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\b', re.I)
RE_TBL = re.compile(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?', re.I)
USERBIND = re.compile(r'\b(user_id|etudiant_id|student_id|professeur_id|prof_id)\b', re.I)


def first_tbl(s, rx):
    m = rx.search(s)
    return m.group(1).lower() if m else None


def scan(path):
    src = open(path, encoding='utf-8').read()
    c1 = []; c2 = []; c3 = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.STRING:
            continue
        s = tok.string
        if not KW.search(s):
            continue
        line = tok.start[0]
        u = s.upper()
        tabs = {m.group(1).lower() for m in RE_TBL.finditer(s)}
        tten = tabs & TEN
        if not tten:
            continue
        if 'school_id' in s:
            continue
        has_where = bool(re.search(r'\bWHERE\b', s, re.I))
        ub = bool(USERBIND.search(s))
        # type
        if re.search(r'\bUPDATE\b', s[:40], re.I):
            t = 'UPDATE'
        elif re.search(r'\bDELETE\b', s[:40], re.I):
            t = 'DELETE'
        elif re.search(r'\bINSERT\b|\bREPLACE\b', s[:40], re.I):
            t = 'INSERT'
        else:
            t = 'SELECT'
        target = first_tbl(s, re.compile(r'\b(?:UPDATE|INTO|FROM)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?', re.I))
        rec = {'line': line, 'type': t, 'target': target,
               'tables': sorted(tten), 'ub': ub, 'where': has_where}
        if t in ('UPDATE', 'DELETE') and has_where and not ub and target in TEN:
            c1.append(rec)
        elif t == 'SELECT' and not has_where and target in TEN:
            c2.append(rec)
        elif t == 'INSERT' and target in TEN:
            c3.append(rec)
    return c1, c2, c3


FILES = ['app.py', 'routes/admissions.py', 'services/admissions_services.py',
         'student_enrollment_service.py', 'services/subscriptions.py',
         'routes/subscriptions.py']
report = {}
for f in FILES:
    try:
        c1, c2, c3 = scan(f)
    except FileNotFoundError:
        continue
    report[f] = {'C1': c1, 'C2': c2, 'C3': c3}
    print(f"\n##### {f}  C1={len(c1)} C2={len(c2)} C3={len(c3)}")
    for tag in ('C1', 'C2', 'C3'):
        for r in report[f][tag]:
            print(f"  {tag} L{r['line']:5} {r['type']:6} -> {r['target']:22} ub={int(r['ub'])} where={int(r['where'])} {r['tables']}")

json.dump(report, open('_worklist.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
tot = {k: sum(len(v[k]) for v in report.values()) for k in ('C1', 'C2', 'C3')}
print("\nTOTAUX:", tot)
