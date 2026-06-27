"""
Création idempotente des tables techniques du module Import.
"""
from db import get_db_connection

DDL = [
    """
    CREATE TABLE IF NOT EXISTS import_jobs (
        id INT PRIMARY KEY AUTO_INCREMENT,
        created_by INT NULL,
        source_type ENUM('excel','csv','word','mysql','api') NOT NULL,
        target_table VARCHAR(80) NOT NULL,
        original_filename VARCHAR(255) NULL,
        stored_path VARCHAR(500) NULL,
        external_config JSON NULL,
        total_rows INT DEFAULT 0,
        valid_rows INT DEFAULT 0,
        invalid_rows INT DEFAULT 0,
        committed_rows INT DEFAULT 0,
        status ENUM('uploaded','parsed','mapped','validated','committed','failed','cancelled')
            DEFAULT 'uploaded',
        error_message TEXT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        committed_at DATETIME NULL,
        INDEX idx_status (status),
        INDEX idx_target (target_table),
        INDEX idx_created_by (created_by)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS import_field_mappings (
        id INT PRIMARY KEY AUTO_INCREMENT,
        job_id INT NOT NULL,
        source_column VARCHAR(150) NOT NULL,
        target_field VARCHAR(150) NULL,
        is_ignored TINYINT(1) DEFAULT 0,
        default_value VARCHAR(255) NULL,
        transform_rule VARCHAR(100) NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (job_id) REFERENCES import_jobs(id) ON DELETE CASCADE,
        UNIQUE KEY uk_job_src (job_id, source_column)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS import_staging_rows (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        job_id INT NOT NULL,
        row_index INT NOT NULL,
        raw_payload JSON NOT NULL,
        mapped_payload JSON NULL,
        is_valid TINYINT(1) DEFAULT 0,
        validation_errors JSON NULL,
        committed TINYINT(1) DEFAULT 0,
        target_row_id INT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (job_id) REFERENCES import_jobs(id) ON DELETE CASCADE,
        INDEX idx_job (job_id),
        INDEX idx_valid (job_id, is_valid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS import_logs (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        job_id INT NULL,
        user_id INT NULL,
        level ENUM('info','warning','error') DEFAULT 'info',
        action VARCHAR(80) NOT NULL,
        message TEXT NULL,
        context JSON NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_job (job_id),
        INDEX idx_level (level)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def ensure_import_tables():
    conn = get_db_connection()
    if not conn:
        return False
    cur = conn.cursor()
    try:
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"[imports.db_init] {e}")
        return False
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    print("✅ Tables imports prêtes" if ensure_import_tables() else "❌ Échec init")
