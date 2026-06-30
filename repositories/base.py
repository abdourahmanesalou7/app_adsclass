"""
Repository de base avec opérations CRUD génériques.

Le pattern Repository sépare la logique d'accès aux données de la logique métier.
Chaque repository gère une table de la base de données.

Usage:
    from repositories.base import BaseRepository

    class UserRepository(BaseRepository):
        table_name = "users"

        def find_by_email(self, email):
            with db_session() as (cursor, conn):
                cursor.execute(
                    f"SELECT * FROM {self.table_name} WHERE email = %s",
                    (email,)
                )
                return cursor.fetchone()

    # Utilisation
    user_repo = UserRepository()
    user = user_repo.find_by_id(123)
    all_users = user_repo.find_all()
    user_repo.create({'nom': 'Dupont', 'email': 'dupont@example.com'})
"""

from core.database import db_session
from core.exceptions import NotFoundError, ValidationError
import tenant


class BaseRepository:
    """
    Repository de base fournissant les opérations CRUD génériques.

    À surcharger dans les repositories spécifiques en définissant `table_name`.

    Attributes:
        table_name (str): Nom de la table dans la base de données
    """

    table_name = None  # À définir dans les sous-classes

    def __init__(self):
        """Initialise le repository et vérifie que table_name est défini."""
        if not self.table_name:
            raise ValueError(
                f"{self.__class__.__name__} doit définir l'attribut 'table_name'"
            )

    def find_by_id(self, id, school_id=None):
        """
        Récupère un enregistrement par son ID (avec filtre tenant).

        Args:
            id (int): ID de l'enregistrement
            school_id (int, optional): ID de l'école (utilise current_school_id si None)

        Returns:
            dict: Enregistrement trouvé

        Raises:
            NotFoundError: Si l'enregistrement n'existe pas

        Example:
            user = user_repo.find_by_id(123)
        """
        school_id = school_id or tenant.current_school_id()

        with db_session() as (cursor, conn):
            # Vérifier si la table a une colonne school_id
            if tenant.is_tenant_table(self.table_name):
                cursor.execute(
                    f"SELECT * FROM {self.table_name} WHERE id = %s AND school_id = %s",
                    (id, school_id)
                )
            else:
                cursor.execute(
                    f"SELECT * FROM {self.table_name} WHERE id = %s",
                    (id,)
                )

            result = cursor.fetchone()

            if not result:
                raise NotFoundError(
                    f"{self.table_name} avec ID {id} introuvable",
                    details={'table': self.table_name, 'id': id}
                )

            return result

    def find_all(self, school_id=None, filters=None, order_by=None, limit=None):
        """
        Récupère tous les enregistrements avec filtres optionnels.

        Args:
            school_id (int, optional): ID de l'école
            filters (dict, optional): Filtres à appliquer {colonne: valeur}
            order_by (str, optional): Clause ORDER BY (ex: "nom ASC")
            limit (int, optional): Nombre maximum de résultats

        Returns:
            list[dict]: Liste des enregistrements

        Example:
            # Tous les étudiants de l'école courante
            etudiants = user_repo.find_all(filters={'role': 'etudiant'})

            # 10 premiers par ordre alphabétique
            users = user_repo.find_all(order_by="nom ASC", limit=10)
        """
        school_id = school_id or tenant.current_school_id()

        with db_session() as (cursor, conn):
            # Construire la requête
            where_clauses = []
            params = []

            # Filtre tenant si applicable
            if tenant.is_tenant_table(self.table_name):
                where_clauses.append("school_id = %s")
                params.append(school_id)

            # Filtres additionnels
            if filters:
                for key, value in filters.items():
                    where_clauses.append(f"{key} = %s")
                    params.append(value)

            # Construire WHERE
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            # Requête complète
            query = f"SELECT * FROM {self.table_name} WHERE {where_sql}"

            if order_by:
                query += f" ORDER BY {order_by}"

            if limit:
                query += f" LIMIT {int(limit)}"

            cursor.execute(query, tuple(params))
            return cursor.fetchall()

    def create(self, data):
        """
        Crée un nouvel enregistrement.

        Args:
            data (dict): Données à insérer {colonne: valeur}

        Returns:
            int: ID de l'enregistrement créé
        """
        if tenant.is_tenant_table(self.table_name):
            data = tenant.with_school(data)

        with db_session(commit=True) as (cursor, conn):
            columns = ", ".join(data.keys())
            placeholders = ", ".join(["%s"] * len(data))

            query = f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})"

            cursor.execute(query, tuple(data.values()))
            return cursor.lastrowid

    def update(self, id, data, school_id=None):
        """
        Met à jour un enregistrement existant.

        Args:
            id (int): ID de l'enregistrement à mettre à jour
            data (dict): Données à mettre à jour {colonne: valeur}
            school_id (int, optional): ID de l'école

        Returns:
            int: Nombre de lignes mises à jour (1 si succès)

        Raises:
            NotFoundError: Si l'enregistrement n'existe pas
        """
        school_id = school_id or tenant.current_school_id()

        with db_session(commit=True) as (cursor, conn):
            set_clause = ", ".join([f"{key} = %s" for key in data.keys()])
            params = list(data.values())

            if tenant.is_tenant_table(self.table_name):
                where_clause = "id = %s AND school_id = %s"
                params.extend([id, school_id])
            else:
                where_clause = "id = %s"
                params.append(id)

            query = f"UPDATE {self.table_name} SET {set_clause} WHERE {where_clause}"
            cursor.execute(query, tuple(params))

            if cursor.rowcount == 0:
                raise NotFoundError(f"{self.table_name} avec ID {id} introuvable")

            return cursor.rowcount

    def delete(self, id, school_id=None):
        """Supprime un enregistrement."""
        school_id = school_id or tenant.current_school_id()

        with db_session(commit=True) as (cursor, conn):
            if tenant.is_tenant_table(self.table_name):
                where_clause = "id = %s AND school_id = %s"
                params = (id, school_id)
            else:
                where_clause = "id = %s"
                params = (id,)

            query = f"DELETE FROM {self.table_name} WHERE {where_clause}"
            cursor.execute(query, params)

            if cursor.rowcount == 0:
                raise NotFoundError(f"{self.table_name} avec ID {id} introuvable")

            return cursor.rowcount

    def count(self, school_id=None, filters=None):
        """Compte le nombre d'enregistrements."""
        school_id = school_id or tenant.current_school_id()

        with db_session() as (cursor, conn):
            where_clauses = []
            params = []

            if tenant.is_tenant_table(self.table_name):
                where_clauses.append("school_id = %s")
                params.append(school_id)

            if filters:
                for key, value in filters.items():
                    where_clauses.append(f"{key} = %s")
                    params.append(value)

            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            query = f"SELECT COUNT(*) as count FROM {self.table_name} WHERE {where_sql}"

            cursor.execute(query, tuple(params))
            result = cursor.fetchone()
            return result['count'] if result else 0

    def exists(self, id, school_id=None):
        """Vérifie si un enregistrement existe."""
        try:
            self.find_by_id(id, school_id)
            return True
        except NotFoundError:
            return False
        # Ajouter school_id si applicable
