# -*- coding: utf-8 -*-
"""Filet de securite — isolation multi-tenant (P0).

Verifie qu'une ecole ne peut ni lire, ni modifier, ni supprimer, ni creer
dans le perimetre d'une autre ecole. Ces tests echouent AVANT C1/C2/C3
et passent APRES (preuve que seule l'isolation des donnees change).

Lancement :  python -m unittest tests.test_tenant_isolation
"""
import os
import sys
import glob
import uuid
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402  (import = chargement de l'app Flask)
from flask import g  # noqa: E402
from db import get_db_connection  # noqa: E402
from routes import chatbot_student as chatbot  # noqa: E402

SCHOOL_A = 1
SCHOOL_B = 2
TAG = "_ISOTEST_"
UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")


def _exec(sql, params=(), fetch=None, commit=False):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        if commit:
            conn.commit()
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()


def _seed_depense(school_id, desc, montant=-100.0):
    return _exec(
        "INSERT INTO depenses (date, description, montant, school_id, annee_academique_id) "
        "VALUES (CURDATE(), %s, %s, %s, NULL)",
        (desc, montant, school_id), commit=True)


def _seed_admin(school_id, email):
    return _exec(
        "INSERT INTO users (nom, prenom, email, password, role, school_id) "
        "VALUES (%s, %s, %s, %s, 'admin', %s)",
        (TAG + "nom", "p", email, "x", school_id), commit=True)


def _seed_student(school_id, nom, email):
    return _exec(
        "INSERT INTO users (nom, prenom, email, password, role, school_id) "
        "VALUES (%s, 'p', %s, 'x', 'etudiant', %s)",
        (nom, email, school_id), commit=True)


def _seed_candidat(school_id, nom):
    return _exec(
        "INSERT INTO admissions_candidats "
        "(nom, prenom, email, statut, probabilite_inscription, score_ia, school_id) "
        "VALUES (%s, 'p', %s, 'candidature', 100, 100, %s)",
        (nom, nom + "@x.io", school_id), commit=True)


def _seed_attestation(school_id, etudiant_id):
    return _exec(
        "INSERT INTO demandes_attestations (etudiant_id, type_attestation, statut, school_id) "
        "VALUES (%s, %s, 'en_attente', %s)",
        (etudiant_id, TAG + "att", school_id), commit=True)


def _seed_professor(school_id, email):
    return _exec(
        "INSERT INTO users (nom, prenom, email, password, role, school_id) "
        "VALUES (%s, 'p', %s, 'x', 'professeur', %s)",
        (TAG + "prof", email, school_id), commit=True)


def _seed_course(school_id, professeur_id, nom_cours, filiere=TAG + "Filiere"):
    return _exec(
        "INSERT INTO courses (nom_cours, professeur_id, filiere, start, end, school_id) "
        "VALUES (%s, %s, %s, NOW(), NOW(), %s)",
        (nom_cours, professeur_id, filiere, school_id), commit=True)


def _seed_document(school_id, course_id, professeur_id, titre, chemin_fichier):
    return _exec(
        "INSERT INTO documents "
        "(course_id, professeur_id, titre, nom_fichier, chemin_fichier, visible, school_id) "
        "VALUES (%s, %s, %s, %s, %s, 1, %s)",
        (course_id, professeur_id, titre, chemin_fichier, chemin_fichier, school_id), commit=True)


def _seed_enrollment(school_id, user_id, course_id):
    return _exec(
        "INSERT INTO emploi_temps (user_id, course_id, role, school_id) "
        "VALUES (%s, %s, 'etudiant', %s)",
        (user_id, course_id, school_id), commit=True)


def _make_pdf(filename):
    """Cree un fichier PDF minimal dans uploads/ (le contenu importe peu)."""
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    with open(os.path.join(UPLOADS_DIR, filename), "wb") as fh:
        fh.write(b"%PDF-1.4\n%%EOF\n")
    return filename


def _sign_student_qr(student_data):
    """Reproduit la signature d'une carte etudiant (secret global ADSCLASS).

    Permet de simuler un QR *valablement signe* d'une autre ecole : seule
    l'isolation par school_id doit alors empecher son usage cross-tenant."""
    import json
    import hashlib
    data_to_verify = {
        "user_id": student_data.get("user_id"),
        "nom": student_data.get("nom"),
        "prenom": student_data.get("prenom"),
        "filiere": student_data.get("filiere"),
        "niveau": student_data.get("niveau"),
        "type": student_data.get("type"),
        "timestamp": student_data.get("timestamp"),
    }
    data_string = json.dumps(data_to_verify, sort_keys=True)
    return hashlib.sha256(f"{data_string}ADSCLASS_SECRET_2024".encode()).hexdigest()[:16]


class TenantIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        appmod.app.config["TESTING"] = True
        cls.client = appmod.app.test_client()
        _exec("INSERT IGNORE INTO schools (id, nom, code, statut) "
              "VALUES (%s, %s, %s, 'active')",
              (SCHOOL_B, TAG + "EcoleB", TAG + "B"), commit=True)

    @classmethod
    def tearDownClass(cls):
        _exec("DELETE FROM depenses WHERE description LIKE %s", (TAG + "%",), commit=True)
        # demandes_attestations avant users (FK etudiant_id -> users.id)
        _exec("DELETE FROM demandes_attestations WHERE type_attestation = %s", (TAG + "att",), commit=True)
        _exec("DELETE FROM admissions_candidats WHERE nom LIKE %s", (TAG + "%",), commit=True)
        # presences avant courses/users (FK course_id/etudiant_id/professeur_id)
        _exec("DELETE FROM presences WHERE etudiant_id IN (SELECT id FROM users WHERE email LIKE %s) "
              "OR professeur_id IN (SELECT id FROM users WHERE email LIKE %s)",
              ("%" + TAG + "%", "%" + TAG + "%"), commit=True)
        # documents avant courses/users (FK course_id/professeur_id)
        _exec("DELETE FROM documents WHERE titre LIKE %s", (TAG + "%",), commit=True)
        # emploi_temps avant courses/users (FK course_id/user_id)
        _exec("DELETE FROM emploi_temps WHERE course_id IN "
              "(SELECT id FROM courses WHERE nom_cours LIKE %s)", (TAG + "%",), commit=True)
        # fichiers PDF de test deposes dans uploads/
        for _f in glob.glob(os.path.join(UPLOADS_DIR, "*" + TAG + "*")):
            try:
                os.remove(_f)
            except OSError:
                pass
        # courses avant users (FK professeur_id -> users.id)
        _exec("DELETE FROM courses WHERE nom_cours LIKE %s", (TAG + "%",), commit=True)
        _exec("DELETE FROM users WHERE email LIKE %s", ("%" + TAG + "%",), commit=True)
        # L'ecole B est conservee si d'autres lignes la referencent (contrainte FK) ;
        # elle sera reutilisee au prochain run via INSERT IGNORE.
        try:
            _exec("DELETE FROM schools WHERE id=%s", (SCHOOL_B,), commit=True)
        except Exception:
            pass

    def _sess(self, school_id, user_id=999999):
        with self.client.session_transaction() as s:
            s["user_id"] = user_id
            s["role"] = "admin"
            s["school_id"] = school_id
            s["annee_academique_id"] = None

    # ---- C1 : suppression cross-tenant interdite ----
    def test_c1_delete_depense_cross_tenant_blocked(self):
        did = _seed_depense(SCHOOL_B, TAG + "delB")
        self._sess(SCHOOL_A)
        self.client.post(f"/admin/depenses/{did}/supprimer")
        row = _exec("SELECT id FROM depenses WHERE id=%s", (did,), fetch="one")
        self.assertIsNotNone(row, "FUITE: une autre ecole a supprime cette depense")

    # ---- C1 : modification cross-tenant interdite ----
    def test_c1_update_depense_cross_tenant_blocked(self):
        did = _seed_depense(SCHOOL_B, TAG + "updB", montant=-100.0)
        self._sess(SCHOOL_A)
        self.client.post(f"/admin/depenses/{did}/modifier",
                         data={"date": "2026-01-01", "description": "HACK", "montant": "500"})
        row = _exec("SELECT montant FROM depenses WHERE id=%s", (did,), fetch="one")
        self.assertEqual(float(row["montant"]), -100.0,
                         "FUITE: une autre ecole a modifie cette depense")

    # ---- C1 : suppression admin cross-tenant interdite ----
    def test_c1_delete_admin_cross_tenant_blocked(self):
        uid = _seed_admin(SCHOOL_B, TAG + "adminB@x.io")
        self._sess(SCHOOL_A, user_id=111111)
        self.client.post(f"/admin/administrateurs/{uid}/delete")
        row = _exec("SELECT id FROM users WHERE id=%s", (uid,), fetch="one")
        self.assertIsNotNone(row, "FUITE: suppression d'un admin d'une autre ecole")

    # ---- C2 : liste isolee par ecole ----
    def test_c2_list_depenses_isolation(self):
        ua = TAG + uuid.uuid4().hex[:8]
        ub = TAG + uuid.uuid4().hex[:8]
        _seed_depense(SCHOOL_A, ua)
        _seed_depense(SCHOOL_B, ub)
        self._sess(SCHOOL_A)
        html = self.client.get("/admin/depenses").get_data(as_text=True)
        self.assertIn(ua, html, "La depense de l'ecole courante doit apparaitre")
        self.assertNotIn(ub, html, "FUITE: depense d'une autre ecole listee")

    # ---- C3 : creation rattachee a la bonne ecole ----
    def test_c3_add_depense_sets_school_id(self):
        marker = TAG + uuid.uuid4().hex[:8]
        self._sess(SCHOOL_B)
        self.client.post("/admin/depenses",
                         data={"date": "2026-01-01", "nature": marker, "montant": "100"})
        row = _exec("SELECT school_id FROM depenses WHERE description=%s", (marker,), fetch="one")
        self.assertIsNotNone(row, "La depense n'a pas ete creee")
        self.assertEqual(row["school_id"], SCHOOL_B,
                         "FUITE: depense creee dans la mauvaise ecole")

    # ---- C2 : stats admissions (predictions IA) isolees par ecole ----
    def test_c2_admissions_stats_predictions_isolation(self):
        nom_b = TAG + uuid.uuid4().hex[:8]
        nom_a = TAG + uuid.uuid4().hex[:8]
        _seed_candidat(SCHOOL_B, nom_b)
        _seed_candidat(SCHOOL_A, nom_a)
        self._sess(SCHOOL_B)
        html = self.client.get("/admin/admissions/statistiques").get_data(as_text=True)
        self.assertIn(nom_b, html, "Le candidat de l'ecole courante doit apparaitre")
        self.assertNotIn(nom_a, html, "FUITE: candidat d'une autre ecole dans les stats")

    # ---- C2 : portail etudiant — documents de cours isoles par ecole ----
    def test_c2_student_course_documents_cross_tenant_blocked(self):
        prof_b = _seed_professor(SCHOOL_B, TAG + uuid.uuid4().hex[:8] + "@x.io")
        nom = TAG + uuid.uuid4().hex[:8]
        cid = _seed_course(SCHOOL_B, prof_b, nom)
        # Session etudiant de l'ecole A tentant d'ouvrir un cours de l'ecole B
        with self.client.session_transaction() as s:
            s["user_id"] = 222222
            s["role"] = "etudiant"
            s["school_id"] = SCHOOL_A
            s["annee_academique_id"] = None
        html = self.client.get(f"/student/course-documents/{cid}").get_data(as_text=True)
        self.assertNotIn(nom, html,
                         "FUITE: un etudiant a vu un cours d'une autre ecole")

    # ---- C2 : liste des demandes d'attestations isolee par ecole ----
    def test_c2_attestations_list_isolation(self):
        nom_b = TAG + uuid.uuid4().hex[:8]
        nom_a = TAG + uuid.uuid4().hex[:8]
        sb = _seed_student(SCHOOL_B, nom_b, nom_b + "@x.io")
        sa = _seed_student(SCHOOL_A, nom_a, nom_a + "@x.io")
        _seed_attestation(SCHOOL_B, sb)
        _seed_attestation(SCHOOL_A, sa)
        self._sess(SCHOOL_B)
        html = self.client.get("/scolarite/attestations").get_data(as_text=True)
        self.assertIn(nom_b, html, "La demande de l'ecole courante doit apparaitre")
        self.assertNotIn(nom_a, html, "FUITE: demande d'une autre ecole listee")

    # ---- IA : base documentaire du chatbot isolee par ecole (BDD uniquement) ----
    def test_c2_chatbot_db_documents_school_isolation(self):
        prof_b = _seed_professor(SCHOOL_B, TAG + uuid.uuid4().hex[:8] + "@x.io")
        cid_b = _seed_course(SCHOOL_B, prof_b, TAG + uuid.uuid4().hex[:8])
        fname = uuid.uuid4().hex[:8] + TAG + ".pdf"
        titre_b = TAG + uuid.uuid4().hex[:8]
        _make_pdf(fname)
        _seed_document(SCHOOL_B, cid_b, prof_b, titre_b, fname)

        with appmod.app.test_request_context():
            g.school_id = SCHOOL_A
            titres_a = [d["titre"] for d in chatbot.get_db_pdf_documents(None)]
            g.school_id = SCHOOL_B
            titres_b = [d["titre"] for d in chatbot.get_db_pdf_documents(None)]

        self.assertNotIn(titre_b, titres_a,
                         "FUITE: un PDF d'une autre ecole expose au chatbot")
        self.assertIn(titre_b, titres_b,
                      "Le PDF de l'ecole courante doit etre disponible")

    # ---- IA : base documentaire bornee a l'inscription de l'etudiant ----
    def test_c2_chatbot_db_documents_student_enrollment_scope(self):
        prof_b = _seed_professor(SCHOOL_B, TAG + uuid.uuid4().hex[:8] + "@x.io")
        cid_b = _seed_course(SCHOOL_B, prof_b, TAG + uuid.uuid4().hex[:8])
        fname = uuid.uuid4().hex[:8] + TAG + ".pdf"
        titre_b = TAG + uuid.uuid4().hex[:8]
        _make_pdf(fname)
        _seed_document(SCHOOL_B, cid_b, prof_b, titre_b, fname)
        stu = _seed_student(SCHOOL_B, TAG + uuid.uuid4().hex[:8],
                            TAG + uuid.uuid4().hex[:8] + "@x.io")

        with appmod.app.test_request_context():
            g.school_id = SCHOOL_B
            avant = [d["titre"] for d in chatbot.get_db_pdf_documents(stu)]
            _seed_enrollment(SCHOOL_B, stu, cid_b)
            apres = [d["titre"] for d in chatbot.get_db_pdf_documents(stu)]

        self.assertNotIn(titre_b, avant,
                         "FUITE: PDF accessible a un etudiant non inscrit")
        self.assertIn(titre_b, apres,
                      "Apres inscription, le PDF du cours doit etre disponible")

    # ---- IA : cle de cache de la base de connaissances isolee par ecole ----
    def test_c2_chatbot_knowledge_cache_key_school_isolation(self):
        with appmod.app.test_request_context():
            g.school_id = SCHOOL_A
            key_a = chatbot._knowledge_cache_key(42)
            g.school_id = SCHOOL_B
            key_b = chatbot._knowledge_cache_key(42)
        self.assertNotEqual(key_a, key_b,
                            "FUITE: meme cle de cache IA pour deux ecoles")
        self.assertIn(str(SCHOOL_A), key_a)
        self.assertIn(str(SCHOOL_B), key_b)

    # ---- Documents : un professeur A ne peut pas telecharger un doc de l'ecole B ----
    def test_c2_professor_download_cross_tenant_blocked(self):
        prof_b = _seed_professor(SCHOOL_B, TAG + uuid.uuid4().hex[:8] + "@x.io")
        cid_b = _seed_course(SCHOOL_B, prof_b, TAG + uuid.uuid4().hex[:8])
        fname = uuid.uuid4().hex[:8] + TAG + ".pdf"
        _make_pdf(fname)
        doc_b = _seed_document(SCHOOL_B, cid_b, prof_b, TAG + uuid.uuid4().hex[:8], fname)
        with self.client.session_transaction() as s:
            s["user_id"] = 333333
            s["role"] = "professeur"
            s["school_id"] = SCHOOL_A
            s["annee_academique_id"] = None
        resp = self.client.get(f"/download/{doc_b}")
        self.assertNotEqual(resp.status_code, 200,
                            "FUITE: telechargement d'un document d'une autre ecole")
        self.assertNotIn("attachment", resp.headers.get("Content-Disposition", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
