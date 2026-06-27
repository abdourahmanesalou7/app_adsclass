"""
SaaS Subscriptions ADSClass — Phase 4
Idempotent : sûr à ré-exécuter.

1. Crée subscription_plans (catalogue) + seed 4 plans
2. Crée school_subscriptions (1 abonnement actif par école)
3. Crée subscription_payments (historique paiements)
4. Attribue automatiquement un abonnement "Pro" 365 jours à l'École 1
   pour ne casser aucun accès existant.
"""
from datetime import datetime, timedelta
from db import get_db_connection


PLANS = [
    ('TRIAL',    'Essai gratuit', 0,       0,        14,  50,   10,
     '["dashboard","students","attendance"]',
     '["Période d\'essai 14 jours","50 étudiants max","Fonctionnalités de base"]'),
    ('STARTER',  'Starter',       250000,   2500000,   30,  200,  30,
     '["dashboard","students","attendance","grades","payments"]',
     '["200 étudiants","Notes et présences","Paiements","Support email"]'),
    ('STANDARD', 'Standard',      300000,   3000000,   30,  500,  80,
     '["dashboard","students","attendance","grades","payments","admissions","attestations","imports"]',
     '["500 étudiants","Admissions CRM","Attestations","Imports avancés","Support prioritaire"]'),
    ('PRO',      'Pro',           400000,  4000000,  30,  9999, 999,
     '["dashboard","students","attendance","grades","payments","admissions","attestations","imports","analytics","chatbot","blockchain"]',
     '["Étudiants illimités","Tous modules","Analytics IA","Chatbot","Blockchain","Support 24/7"]'),
]


def main():
    conn = get_db_connection()
    if not conn:
        print("❌ Connexion DB impossible")
        return 1
    cur = conn.cursor()
    try:
        # === 1. subscription_plans ===
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscription_plans (
                id INT PRIMARY KEY AUTO_INCREMENT,
                code VARCHAR(40) UNIQUE NOT NULL,
                nom VARCHAR(100) NOT NULL,
                prix_mensuel DECIMAL(10,2) DEFAULT 0,
                prix_annuel DECIMAL(10,2) DEFAULT 0,
                duree_defaut_jours INT DEFAULT 30,
                max_students INT DEFAULT 100,
                max_users INT DEFAULT 20,
                modules JSON NULL,
                features JSON NULL,
                devise VARCHAR(10) DEFAULT 'XOF',
                actif TINYINT(1) DEFAULT 1,
                date_creation DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        print("✅ Table subscription_plans prête")

        # Seed plans (upsert sur code)
        for code, nom, pm, pa, dur, ms, mu, mods, feats in PLANS:
            cur.execute("""
                INSERT INTO subscription_plans
                  (code, nom, prix_mensuel, prix_annuel, duree_defaut_jours,
                   max_students, max_users, modules, features)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  nom=VALUES(nom), prix_mensuel=VALUES(prix_mensuel),
                  prix_annuel=VALUES(prix_annuel), duree_defaut_jours=VALUES(duree_defaut_jours),
                  max_students=VALUES(max_students), max_users=VALUES(max_users),
                  modules=VALUES(modules), features=VALUES(features)
            """, (code, nom, pm, pa, dur, ms, mu, mods, feats))
        print(f"✅ {len(PLANS)} plans seedés")

        # === 2. school_subscriptions ===
        cur.execute("""
            CREATE TABLE IF NOT EXISTS school_subscriptions (
                id INT PRIMARY KEY AUTO_INCREMENT,
                school_id INT NOT NULL,
                plan_id INT NOT NULL,
                started_at DATETIME NOT NULL,
                ends_at DATETIME NOT NULL,
                status ENUM('trial','active','expired','cancelled','suspended') DEFAULT 'active',
                auto_renew TINYINT(1) DEFAULT 0,
                payment_method VARCHAR(40) NULL,
                last_paid_at DATETIME NULL,
                amount_paid DECIMAL(10,2) DEFAULT 0,
                notes TEXT NULL,
                created_by INT NULL,
                date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
                date_modification DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_sub_school (school_id),
                INDEX idx_sub_status (status),
                INDEX idx_sub_ends (ends_at),
                CONSTRAINT fk_sub_school FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE,
                CONSTRAINT fk_sub_plan FOREIGN KEY (plan_id) REFERENCES subscription_plans(id) ON DELETE RESTRICT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        print("✅ Table school_subscriptions prête")

        # === 3. subscription_payments ===
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscription_payments (
                id INT PRIMARY KEY AUTO_INCREMENT,
                subscription_id INT NOT NULL,
                school_id INT NOT NULL,
                montant DECIMAL(10,2) NOT NULL,
                devise VARCHAR(10) DEFAULT 'XOF',
                methode VARCHAR(40) DEFAULT 'manuel',
                reference VARCHAR(150) NULL,
                statut ENUM('en_attente','payé','échec','remboursé') DEFAULT 'payé',
                paid_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_by INT NULL,
                notes TEXT NULL,
                INDEX idx_pay_school (school_id),
                INDEX idx_pay_sub (subscription_id),
                CONSTRAINT fk_pay_sub FOREIGN KEY (subscription_id) REFERENCES school_subscriptions(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        print("✅ Table subscription_payments prête")

        conn.commit()

        # === 4. Abonnement Pro auto pour École 1 (si aucun abonnement actif) ===
        cur.execute("""
            SELECT id FROM school_subscriptions
            WHERE school_id=1 AND status IN ('active','trial') AND ends_at > NOW()
            LIMIT 1
        """)
        if not cur.fetchone():
            cur.execute("SELECT id FROM subscription_plans WHERE code='PRO' LIMIT 1")
            row = cur.fetchone()
            if row:
                plan_id = row[0]
                started = datetime.now()
                ends = started + timedelta(days=365)
                cur.execute("""
                    INSERT INTO school_subscriptions
                      (school_id, plan_id, started_at, ends_at, status, auto_renew, notes)
                    VALUES (1, %s, %s, %s, 'active', 1, 'Abonnement initial automatique École par défaut')
                """, (plan_id, started, ends))
                conn.commit()
                print(f"✅ Abonnement Pro 365j attribué à École 1 (jusqu'au {ends.strftime('%Y-%m-%d')})")
        else:
            print("ℹ École 1 a déjà un abonnement actif")

        return 0
    except Exception as e:
        conn.rollback()
        print(f"❌ Erreur : {e}")
        return 2
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
