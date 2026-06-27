"""
Module Attestations Scolaires — AdsClass
Workflow d'approbation (étudiant → scolarité → directeur) avec signature blockchain SHA-256.
"""

import os
import tenant


def init_attestations_tables(get_db_connection):
    """Créer les tables pour le système d'attestations avec workflow d'approbation"""
    try:
        conn = get_db_connection()
        if not conn:
            return

        cursor = conn.cursor()

        # Table des demandes d'attestations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS demandes_attestations (
                id INT PRIMARY KEY AUTO_INCREMENT,
                etudiant_id INT NOT NULL,
                type_attestation VARCHAR(100) NOT NULL,
                motif TEXT,
                date_naissance DATE NULL,
                statut ENUM('en_attente', 'approuve_scolarite', 'signe_directeur', 'delivre', 'rejete') DEFAULT 'en_attente',
                date_demande DATETIME DEFAULT CURRENT_TIMESTAMP,
                date_approbation_scolarite DATETIME NULL,
                approuve_par_scolarite INT NULL,
                commentaire_scolarite TEXT NULL,
                date_signature_directeur DATETIME NULL,
                signe_par_directeur INT NULL,
                signature_blockchain VARCHAR(255) NULL,
                commentaire_directeur TEXT NULL,
                date_delivrance DATETIME NULL,
                numero_attestation VARCHAR(50) UNIQUE,
                annee_academique_id INT NULL,
                FOREIGN KEY (etudiant_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (approuve_par_scolarite) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (signe_par_directeur) REFERENCES users(id) ON DELETE SET NULL,
                INDEX idx_statut (statut),
                INDEX idx_etudiant (etudiant_id),
                INDEX idx_date_demande (date_demande)
            )
        """)

        # Table pour l'historique des actions (audit trail)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS attestations_historique (
                id INT PRIMARY KEY AUTO_INCREMENT,
                demande_id INT NOT NULL,
                action VARCHAR(100) NOT NULL,
                effectue_par INT NOT NULL,
                role_utilisateur VARCHAR(50),
                commentaire TEXT,
                date_action DATETIME DEFAULT CURRENT_TIMESTAMP,
                donnees_supplementaires TEXT,
                FOREIGN KEY (demande_id) REFERENCES demandes_attestations(id) ON DELETE CASCADE,
                FOREIGN KEY (effectue_par) REFERENCES users(id) ON DELETE CASCADE,
                INDEX idx_demande (demande_id),
                INDEX idx_date (date_action)
            )
        """)

        # Table pour les signatures blockchain (vérification)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS attestations_blockchain (
                id INT PRIMARY KEY AUTO_INCREMENT,
                demande_id INT NOT NULL UNIQUE,
                hash_document VARCHAR(255) NOT NULL,
                hash_signature VARCHAR(255) NOT NULL,
                timestamp_signature DATETIME NOT NULL,
                cle_publique TEXT,
                donnees_verification TEXT,
                est_valide BOOLEAN DEFAULT TRUE,
                date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (demande_id) REFERENCES demandes_attestations(id) ON DELETE CASCADE,
                INDEX idx_hash (hash_signature)
            )
        """)

        # Ajouter la colonne date_naissance si elle n'existe pas
        try:
            cursor.execute("""
                ALTER TABLE demandes_attestations
                ADD COLUMN date_naissance DATE NULL AFTER motif
            """)
            print("✅ Colonne date_naissance ajoutée")
        except:
            pass  # La colonne existe déjà

        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Tables attestations initialisées avec succès")
    except Exception as e:
        print(f"❌ Erreur lors de l'initialisation des tables attestations: {e}")


def register_attestations_routes(app, deps):
    """Enregistrer toutes les routes Attestations sur l'application Flask."""
    from flask import render_template, request, redirect, url_for, session, flash, jsonify

    get_db_connection = deps['get_db_connection']
    login_required = deps['login_required']
    get_current_year_id = deps['get_current_year_id']

    # === FONCTIONS UTILITAIRES ===

    def generer_numero_attestation():
        """Générer un numéro unique d'attestation"""
        import random
        import string
        from datetime import datetime

        annee = datetime.now().year
        mois = datetime.now().strftime('%m')
        random_part = ''.join(random.choices(string.digits, k=6))
        return f"ATT-{annee}{mois}-{random_part}"

    def generer_signature_blockchain(demande_id, etudiant_data, directeur_id):
        """Générer une signature blockchain sécurisée avec hash SHA-256"""
        import hashlib
        import json
        from datetime import datetime

        # Données à hasher
        timestamp = datetime.now().isoformat()
        data_to_hash = {
            'demande_id': demande_id,
            'etudiant_id': etudiant_data.get('id'),
            'etudiant_nom': etudiant_data.get('nom'),
            'etudiant_prenom': etudiant_data.get('prenom'),
            'directeur_id': directeur_id,
            'timestamp': timestamp
        }

        # Créer le hash du document
        document_string = json.dumps(data_to_hash, sort_keys=True)
        hash_document = hashlib.sha256(document_string.encode()).hexdigest()

        # Créer la signature (hash du hash + clé secrète, externalisée en .env — P1.1)
        secret_key = os.environ.get('ATTESTATION_SECRET', 'ADSCLASS_ATTESTATION_SECRET_2024')
        signature_string = f"{hash_document}{secret_key}{timestamp}"
        hash_signature = hashlib.sha256(signature_string.encode()).hexdigest()

        return {
            'hash_document': hash_document,
            'hash_signature': hash_signature,
            'timestamp': timestamp,
            'donnees_verification': json.dumps(data_to_hash)
        }

    def verifier_signature_blockchain(demande_id):
        """Vérifier l'authenticité d'une signature blockchain"""
        try:
            conn = get_db_connection()
            if not conn:
                return False

            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT * FROM attestations_blockchain
                WHERE demande_id = %s AND est_valide = TRUE
            """, (demande_id,))

            blockchain_data = cursor.fetchone()
            conn.close()

            return blockchain_data is not None
        except:
            return False

    def ajouter_historique_attestation(demande_id, action, effectue_par, commentaire=None):
        """Ajouter une entrée dans l'historique des attestations"""
        try:
            conn = get_db_connection()
            if not conn:
                return

            cursor = conn.cursor()

            # Récupérer le rôle de l'utilisateur
            cursor.execute("SELECT role FROM users WHERE id = %s", (effectue_par,))
            user = cursor.fetchone()
            role = user[0] if user else 'inconnu'

            cursor.execute("""
                INSERT INTO attestations_historique
                (demande_id, action, effectue_par, role_utilisateur, commentaire)
                VALUES (%s, %s, %s, %s, %s)
            """, (demande_id, action, effectue_par, role, commentaire))

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erreur ajout historique: {e}")

    # === ROUTES ÉTUDIANT ===

    @app.route('/student/attestations')
    @login_required
    def student_attestations():
        """Page de gestion des attestations pour les étudiants"""
        if session.get('role') != 'etudiant':
            flash("Accès non autorisé.", "danger")
            return redirect(url_for('login'))

        try:
            conn = get_db_connection()
            if not conn:
                flash("Erreur de connexion à la base de données.", "danger")
                return redirect(url_for('student_dashboard'))

            cursor = conn.cursor(dictionary=True)
            etudiant_id = session.get('user_id')

            # Récupérer toutes les demandes de l'étudiant
            cursor.execute("""
                SELECT da.*,
                       us.nom as scolarite_nom, us.prenom as scolarite_prenom,
                       ud.nom as directeur_nom, ud.prenom as directeur_prenom
                FROM demandes_attestations da
                LEFT JOIN users us ON da.approuve_par_scolarite = us.id
                LEFT JOIN users ud ON da.signe_par_directeur = ud.id
                WHERE da.etudiant_id = %s
                ORDER BY da.date_demande DESC
            """, (etudiant_id,))

            demandes = cursor.fetchall()

            # Statistiques
            stats = {
                'total': len(demandes),
                'en_attente': len([d for d in demandes if d['statut'] == 'en_attente']),
                'approuvees': len([d for d in demandes if d['statut'] == 'approuve_scolarite']),
                'signees': len([d for d in demandes if d['statut'] == 'signe_directeur']),
                'delivrees': len([d for d in demandes if d['statut'] == 'delivre']),
                'rejetees': len([d for d in demandes if d['statut'] == 'rejete'])
            }

            conn.close()

            return render_template('student_attestations.html',
                                 demandes=demandes,
                                 stats=stats)

        except Exception as e:
            print(f"Erreur student_attestations: {e}")
            flash("Une erreur est survenue.", "danger")
            return redirect(url_for('student_dashboard'))

    @app.route('/student/attestations/demander', methods=['POST'])
    @login_required
    def student_demander_attestation():
        """Créer une nouvelle demande d'attestation"""
        if session.get('role') != 'etudiant':
            return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403

        try:
            type_attestation = request.form.get('type_attestation')
            motif = request.form.get('motif', '')
            date_naissance = request.form.get('date_naissance')

            if not type_attestation:
                return jsonify({'success': False, 'message': 'Type d\'attestation requis'}), 400

            if not date_naissance:
                return jsonify({'success': False, 'message': 'Date de naissance requise'}), 400

            conn = get_db_connection()
            if not conn:
                return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

            cursor = conn.cursor()
            etudiant_id = session.get('user_id')
            year_id = get_current_year_id()
            numero = generer_numero_attestation()

            # Créer la demande
            cursor.execute("""
                INSERT INTO demandes_attestations
                (etudiant_id, type_attestation, motif, date_naissance, numero_attestation, annee_academique_id, statut, school_id)
                VALUES (%s, %s, %s, %s, %s, %s, 'en_attente', %s)
            """, (etudiant_id, type_attestation, motif, date_naissance, numero, year_id, tenant.current_school_id()))

            demande_id = cursor.lastrowid

            # Ajouter à l'historique
            ajouter_historique_attestation(demande_id, 'Demande créée', etudiant_id, f"Type: {type_attestation}")

            conn.commit()
            conn.close()

            flash(f"Votre demande d'attestation a été créée avec succès. Numéro: {numero}", "success")
            return jsonify({'success': True, 'numero': numero})

        except Exception as e:
            print(f"Erreur demande attestation: {e}")
            return jsonify({'success': False, 'message': 'Erreur lors de la création'}), 500

    @app.route('/student/attestation/<int:demande_id>/delete', methods=['POST'])
    @login_required
    def student_delete_attestation(demande_id):
        """Supprimer une demande d'attestation (uniquement si en_attente ou rejetée)"""
        if session.get('role') != 'etudiant':
            return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403

        try:
            conn = get_db_connection()
            if not conn:
                return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

            cursor = conn.cursor(dictionary=True)
            etudiant_id = session.get('user_id')

            # Vérifier que la demande appartient à l'étudiant et qu'elle peut être supprimée
            cursor.execute("""
                SELECT id, numero_attestation, statut, type_attestation
                FROM demandes_attestations
                WHERE id = %s AND etudiant_id = %s
            """, (demande_id, etudiant_id))

            demande = cursor.fetchone()

            if not demande:
                conn.close()
                return jsonify({'success': False, 'message': 'Demande non trouvée'}), 404

            # Vérifier que le statut permet la suppression
            if demande['statut'] not in ['en_attente', 'rejete']:
                conn.close()
                return jsonify({
                    'success': False,
                    'message': 'Impossible de supprimer une demande déjà approuvée ou signée'
                }), 400

            # Supprimer d'abord l'historique associé
            cursor.execute("""
                DELETE FROM attestations_historique
                WHERE demande_id = %s
            """, (demande_id,))

            # Supprimer la demande
            cursor.execute("""
                DELETE FROM demandes_attestations
                WHERE id = %s AND school_id = %s
            """, (demande_id, tenant.current_school_id()))

            conn.commit()
            conn.close()

            return jsonify({
                'success': True,
                'message': f"La demande {demande['numero_attestation']} a été supprimée avec succès"
            })

        except Exception as e:
            print(f"Erreur suppression attestation: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'message': 'Erreur lors de la suppression'}), 500

    # === ROUTES SERVICE SCOLARITÉ ===

    @app.route('/scolarite/attestations')
    @login_required
    def scolarite_attestations():
        """Dashboard scolarité pour gérer les demandes d'attestations"""
        if session.get('role') not in ['admin', 'scolarite']:
            flash("Accès non autorisé.", "danger")
            return redirect(url_for('login'))

        try:
            conn = get_db_connection()
            if not conn:
                flash("Erreur de connexion.", "danger")
                return redirect(url_for('admin_home'))

            cursor = conn.cursor(dictionary=True)

            # Récupérer toutes les demandes
            cursor.execute("""
                SELECT da.*,
                       u.nom, u.prenom, u.email, u.filiere, u.niveau,
                       us.nom as scolarite_nom, us.prenom as scolarite_prenom
                FROM demandes_attestations da
                JOIN users u ON da.etudiant_id = u.id
                LEFT JOIN users us ON da.approuve_par_scolarite = us.id
                WHERE da.school_id = %s
                ORDER BY
                    CASE da.statut
                        WHEN 'en_attente' THEN 1
                        WHEN 'approuve_scolarite' THEN 2
                        WHEN 'signe_directeur' THEN 3
                        WHEN 'delivre' THEN 4
                        WHEN 'rejete' THEN 5
                    END,
                    da.date_demande DESC
            """, (tenant.current_school_id(),))

            demandes = cursor.fetchall()

            # Statistiques
            stats = {
                'total': len(demandes),
                'en_attente': len([d for d in demandes if d['statut'] == 'en_attente']),
                'approuvees': len([d for d in demandes if d['statut'] == 'approuve_scolarite']),
                'signees': len([d for d in demandes if d['statut'] == 'signe_directeur']),
                'delivrees': len([d for d in demandes if d['statut'] == 'delivre']),
                'rejetees': len([d for d in demandes if d['statut'] == 'rejete'])
            }

            conn.close()

            return render_template('scolarite_attestations.html',
                                 demandes=demandes,
                                 stats=stats)

        except Exception as e:
            print(f"Erreur scolarite_attestations: {e}")
            flash("Une erreur est survenue.", "danger")
            return redirect(url_for('admin_home'))

    @app.route('/scolarite/attestations/<int:demande_id>/approuver', methods=['POST'])
    @login_required
    def scolarite_approuver_attestation(demande_id):
        """Approuver une demande d'attestation"""
        if session.get('role') not in ['admin', 'scolarite']:
            return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403

        try:
            commentaire = request.form.get('commentaire', '')

            conn = get_db_connection()
            if not conn:
                return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

            cursor = conn.cursor()
            user_id = session.get('user_id')

            # Mettre à jour la demande
            cursor.execute("""
                UPDATE demandes_attestations
                SET statut = 'approuve_scolarite',
                    date_approbation_scolarite = NOW(),
                    approuve_par_scolarite = %s,
                    commentaire_scolarite = %s
                WHERE id = %s AND statut = 'en_attente' AND school_id = %s
            """, (user_id, commentaire, demande_id, tenant.current_school_id()))

            if cursor.rowcount == 0:
                conn.close()
                return jsonify({'success': False, 'message': 'Demande introuvable ou déjà traitée'}), 404

            # Ajouter à l'historique
            ajouter_historique_attestation(demande_id, 'Approuvée par la scolarité', user_id, commentaire)

            conn.commit()
            conn.close()

            flash("Demande approuvée avec succès. En attente de signature du directeur.", "success")
            return jsonify({'success': True})

        except Exception as e:
            print(f"Erreur approbation: {e}")
            return jsonify({'success': False, 'message': 'Erreur lors de l\'approbation'}), 500

    @app.route('/scolarite/attestations/<int:demande_id>/rejeter', methods=['POST'])
    @login_required
    def scolarite_rejeter_attestation(demande_id):
        """Rejeter une demande d'attestation"""
        if session.get('role') not in ['admin', 'scolarite']:
            return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403

        try:
            motif_rejet = request.form.get('motif_rejet', '')

            if not motif_rejet:
                return jsonify({'success': False, 'message': 'Motif de rejet requis'}), 400

            conn = get_db_connection()
            if not conn:
                return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

            cursor = conn.cursor()
            user_id = session.get('user_id')

            cursor.execute("""
                UPDATE demandes_attestations
                SET statut = 'rejete',
                    commentaire_scolarite = %s
                WHERE id = %s AND statut = 'en_attente' AND school_id = %s
            """, (motif_rejet, demande_id, tenant.current_school_id()))

            if cursor.rowcount == 0:
                conn.close()
                return jsonify({'success': False, 'message': 'Demande introuvable'}), 404

            ajouter_historique_attestation(demande_id, 'Rejetée par la scolarité', user_id, motif_rejet)

            conn.commit()
            conn.close()

            flash("Demande rejetée.", "warning")
            return jsonify({'success': True})

        except Exception as e:
            print(f"Erreur rejet: {e}")
            return jsonify({'success': False, 'message': 'Erreur lors du rejet'}), 500

    # === ROUTES DIRECTEUR ===

    @app.route('/directeur/attestations')
    @login_required
    def directeur_attestations():
        """Dashboard directeur pour signer les attestations"""
        if session.get('role') not in ['admin', 'directeur']:
            flash("Accès non autorisé.", "danger")
            return redirect(url_for('login'))

        try:
            conn = get_db_connection()
            if not conn:
                flash("Erreur de connexion.", "danger")
                return redirect(url_for('admin_home'))

            cursor = conn.cursor(dictionary=True)

            # Récupérer les demandes approuvées par la scolarité
            cursor.execute("""
                SELECT da.*,
                       u.nom, u.prenom, u.email, u.filiere, u.niveau,
                       us.nom as scolarite_nom, us.prenom as scolarite_prenom,
                       ud.nom as directeur_nom, ud.prenom as directeur_prenom
                FROM demandes_attestations da
                JOIN users u ON da.etudiant_id = u.id
                LEFT JOIN users us ON da.approuve_par_scolarite = us.id
                LEFT JOIN users ud ON da.signe_par_directeur = ud.id
                WHERE da.statut IN ('approuve_scolarite', 'signe_directeur', 'delivre')
                AND da.school_id = %s
                ORDER BY
                    CASE da.statut
                        WHEN 'approuve_scolarite' THEN 1
                        WHEN 'signe_directeur' THEN 2
                        WHEN 'delivre' THEN 3
                    END,
                    da.date_approbation_scolarite DESC
            """, (tenant.current_school_id(),))

            demandes = cursor.fetchall()

            # Statistiques
            stats = {
                'total': len(demandes),
                'a_signer': len([d for d in demandes if d['statut'] == 'approuve_scolarite']),
                'signees': len([d for d in demandes if d['statut'] == 'signe_directeur']),
                'delivrees': len([d for d in demandes if d['statut'] == 'delivre'])
            }

            conn.close()

            return render_template('directeur_attestations.html',
                                 demandes=demandes,
                                 stats=stats)

        except Exception as e:
            print(f"Erreur directeur_attestations: {e}")
            flash("Une erreur est survenue.", "danger")
            return redirect(url_for('admin_home'))

    @app.route('/directeur/attestations/<int:demande_id>/signer', methods=['POST'])
    @login_required
    def directeur_signer_attestation(demande_id):
        """Signer une attestation avec blockchain"""
        if session.get('role') not in ['admin', 'directeur']:
            return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403

        try:
            commentaire = request.form.get('commentaire', '')

            conn = get_db_connection()
            if not conn:
                return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

            cursor = conn.cursor(dictionary=True)
            user_id = session.get('user_id')

            # Récupérer les données de l'étudiant
            cursor.execute("""
                SELECT da.*, u.id, u.nom, u.prenom, u.email, u.filiere, u.niveau
                FROM demandes_attestations da
                JOIN users u ON da.etudiant_id = u.id
                WHERE da.id = %s AND da.statut = 'approuve_scolarite' AND da.school_id = %s
            """, (demande_id, tenant.current_school_id()))

            demande = cursor.fetchone()

            if not demande:
                conn.close()
                return jsonify({'success': False, 'message': 'Demande introuvable ou déjà signée'}), 404

            # Générer la signature blockchain
            etudiant_data = {
                'id': demande['id'],
                'nom': demande['nom'],
                'prenom': demande['prenom'],
                'email': demande['email']
            }

            blockchain_data = generer_signature_blockchain(demande_id, etudiant_data, user_id)

            # Mettre à jour la demande
            cursor.execute("""
                UPDATE demandes_attestations
                SET statut = 'signe_directeur',
                    date_signature_directeur = NOW(),
                    signe_par_directeur = %s,
                    signature_blockchain = %s,
                    commentaire_directeur = %s
                WHERE id = %s AND school_id = %s
            """, (user_id, blockchain_data['hash_signature'], commentaire, demande_id, tenant.current_school_id()))

            # Enregistrer dans la table blockchain
            cursor.execute("""
                INSERT INTO attestations_blockchain
                (demande_id, hash_document, hash_signature, timestamp_signature, donnees_verification)
                VALUES (%s, %s, %s, %s, %s)
            """, (demande_id, blockchain_data['hash_document'], blockchain_data['hash_signature'],
                  blockchain_data['timestamp'], blockchain_data['donnees_verification']))

            # Ajouter à l'historique
            ajouter_historique_attestation(demande_id, 'Signée par le directeur (Blockchain)', user_id, commentaire)

            conn.commit()
            conn.close()

            flash("Attestation signée avec succès et sécurisée par blockchain.", "success")
            return jsonify({'success': True, 'signature': blockchain_data['hash_signature'][:16] + '...'})

        except Exception as e:
            print(f"Erreur signature: {e}")
            return jsonify({'success': False, 'message': 'Erreur lors de la signature'}), 500

    @app.route('/directeur/attestations/<int:demande_id>/delivrer', methods=['POST'])
    @login_required
    def directeur_delivrer_attestation(demande_id):
        """Marquer une attestation comme délivrée"""
        if session.get('role') not in ['admin', 'directeur']:
            return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403

        try:
            conn = get_db_connection()
            if not conn:
                return jsonify({'success': False, 'message': 'Erreur de connexion'}), 500

            cursor = conn.cursor()
            user_id = session.get('user_id')

            cursor.execute("""
                UPDATE demandes_attestations
                SET statut = 'delivre',
                    date_delivrance = NOW()
                WHERE id = %s AND statut = 'signe_directeur' AND school_id = %s
            """, (demande_id, tenant.current_school_id()))

            if cursor.rowcount == 0:
                conn.close()
                return jsonify({'success': False, 'message': 'Demande introuvable'}), 404

            ajouter_historique_attestation(demande_id, 'Attestation délivrée', user_id)

            conn.commit()
            conn.close()

            flash("Attestation marquée comme délivrée.", "success")
            return jsonify({'success': True})

        except Exception as e:
            print(f"Erreur délivrance: {e}")
            return jsonify({'success': False, 'message': 'Erreur'}), 500

    # === ROUTE D'IMPRESSION / DEBUG ===

    @app.route('/attestation/<int:demande_id>/imprimer')
    @login_required
    def imprimer_attestation(demande_id):
        """Imprimer une attestation signée"""
        try:
            conn = get_db_connection()
            if not conn:
                flash("Erreur de connexion.", "danger")
                return redirect(url_for('login'))

            cursor = conn.cursor(dictionary=True)
            user_id = session.get('user_id')
            user_role = session.get('role')

            # Récupérer la demande avec toutes les informations (date_naissance est déjà dans da.*)
            cursor.execute("""
                SELECT da.*,
                       u.nom, u.prenom, u.email, u.filiere, u.niveau,
                       us.nom as scolarite_nom, us.prenom as scolarite_prenom,
                       ud.nom as directeur_nom, ud.prenom as directeur_prenom,
                       ab.hash_signature, ab.timestamp_signature
                FROM demandes_attestations da
                JOIN users u ON da.etudiant_id = u.id
                LEFT JOIN users us ON da.approuve_par_scolarite = us.id
                LEFT JOIN users ud ON da.signe_par_directeur = ud.id
                LEFT JOIN attestations_blockchain ab ON da.id = ab.demande_id
                WHERE da.id = %s AND da.school_id = %s
            """, (demande_id, tenant.current_school_id()))

            demande = cursor.fetchone()

            if not demande:
                conn.close()
                flash("Attestation introuvable.", "danger")
                return redirect(url_for('login'))

            # Vérifier les permissions
            if user_role == 'etudiant' and demande['etudiant_id'] != user_id:
                conn.close()
                flash("Accès non autorisé.", "danger")
                return redirect(url_for('student_dashboard'))

            # Seules les attestations signées peuvent être imprimées
            if demande['statut'] not in ['signe_directeur', 'delivre']:
                conn.close()
                flash("Cette attestation n'est pas encore signée.", "warning")
                return redirect(url_for('student_attestations') if user_role == 'etudiant' else url_for('admin_home'))

            # Année académique par défaut
            year_name = "2024/2025"

            conn.close()

            # Vérifier la signature blockchain
            signature_valide = verifier_signature_blockchain(demande_id)

            # Configurer la locale française pour les dates
            import locale
            try:
                locale.setlocale(locale.LC_TIME, 'fr_FR.UTF-8')
            except:
                try:
                    locale.setlocale(locale.LC_TIME, 'French_France.1252')
                except:
                    pass  # Utiliser la locale par défaut

            return render_template('attestation_print.html',
                                 demande=demande,
                                 year_name=year_name,
                                 signature_valide=signature_valide)

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"❌ Erreur impression attestation: {e}")
            print(f"Détails: {error_details}")
            flash(f"Une erreur est survenue lors de l'impression: {str(e)}", "danger")
            return redirect(url_for('student_attestations') if session.get('role') == 'etudiant' else url_for('admin_home'))

    @app.route('/attestation/<int:demande_id>/debug')
    @login_required
    def debug_attestation(demande_id):
        """Route de débogage pour voir les données de l'attestation"""
        try:
            conn = get_db_connection()
            if not conn:
                return "Erreur de connexion", 500

            cursor = conn.cursor(dictionary=True)

            # Récupérer la demande avec toutes les informations
            cursor.execute("""
                SELECT da.*,
                       u.nom, u.prenom, u.email, u.filiere, u.niveau,
                       us.nom as scolarite_nom, us.prenom as scolarite_prenom,
                       ud.nom as directeur_nom, ud.prenom as directeur_prenom,
                       ab.hash_signature, ab.timestamp_signature
                FROM demandes_attestations da
                JOIN users u ON da.etudiant_id = u.id
                LEFT JOIN users us ON da.approuve_par_scolarite = us.id
                LEFT JOIN users ud ON da.signe_par_directeur = ud.id
                LEFT JOIN attestations_blockchain ab ON da.id = ab.demande_id
                WHERE da.id = %s AND da.school_id = %s
            """, (demande_id, tenant.current_school_id()))

            demande = cursor.fetchone()
            conn.close()

            if not demande:
                return "Attestation introuvable", 404

            # Afficher toutes les données
            import json
            output = "<h1>Données de l'attestation #" + str(demande_id) + "</h1>"
            output += "<pre>" + json.dumps(dict(demande), indent=2, default=str) + "</pre>"
            return output

        except Exception as e:
            import traceback
            return f"<h1>Erreur</h1><pre>{str(e)}\n\n{traceback.format_exc()}</pre>"
