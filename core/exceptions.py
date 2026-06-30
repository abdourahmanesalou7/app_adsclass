"""
Exceptions personnalisées pour ADSClass.

Hiérarchie des exceptions:
    ADSClassException (base)
    ├── DatabaseError
    ├── NotFoundError
    ├── PermissionDeniedError
    ├── ValidationError
    ├── AuthenticationError
    └── TenantError

Usage:
    from core.exceptions import NotFoundError
    
    if not user:
        raise NotFoundError("Utilisateur introuvable")
"""


class ADSClassException(Exception):
    """
    Exception de base pour toutes les exceptions métier de ADSClass.
    
    Toutes les exceptions custom doivent hériter de cette classe
    pour faciliter la capture globale.
    """
    def __init__(self, message: str = None, details: dict = None):
        self.message = message or "Une erreur est survenue"
        self.details = details or {}
        super().__init__(self.message)


class DatabaseError(ADSClassException):
    """
    Erreur liée à la base de données.
    
    Exemples:
    - Connexion impossible
    - Requête SQL échouée
    - Transaction rollback
    """
    pass


class NotFoundError(ADSClassException):
    """
    Ressource demandée introuvable.
    
    Exemples:
    - Utilisateur #123 n'existe pas
    - Document #456 introuvable
    - Cours #789 supprimé
    """
    pass


class PermissionDeniedError(ADSClassException):
    """
    Permission refusée pour effectuer l'action.
    
    Exemples:
    - Étudiant essaie d'accéder à une route admin
    - Professeur essaie de modifier le cours d'un autre professeur
    - Accès à une ressource d'une autre école (multi-tenant)
    """
    pass


class ValidationError(ADSClassException):
    """
    Erreur de validation des données.
    
    Exemples:
    - Email invalide
    - Champ obligatoire manquant
    - Format de fichier non autorisé
    - Date de fin avant date de début
    """
    def __init__(self, message: str = None, field: str = None, value=None):
        super().__init__(message)
        self.field = field
        self.value = value
        if field:
            self.details['field'] = field
        if value is not None:
            self.details['value'] = str(value)


class AuthenticationError(ADSClassException):
    """
    Erreur d'authentification.
    
    Exemples:
    - Mot de passe incorrect
    - Email inconnu
    - Token expiré
    - Session invalide
    """
    pass


class TenantError(ADSClassException):
    """
    Erreur liée au multi-tenant.
    
    Exemples:
    - school_id manquant
    - Tentative d'accès à une ressource d'une autre école
    - École inexistante
    """
    pass


class BusinessRuleError(ADSClassException):
    """
    Violation d'une règle métier.
    
    Exemples:
    - Inscription après la date limite
    - Paiement déjà effectué
    - Cours déjà finalisé (présences verrouillées)
    """
    pass


class FileUploadError(ADSClassException):
    """
    Erreur lors de l'upload de fichier.
    
    Exemples:
    - Fichier trop volumineux
    - Type de fichier non autorisé
    - Erreur d'écriture disque
    """
    pass
