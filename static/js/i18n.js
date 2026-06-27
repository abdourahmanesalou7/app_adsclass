/**
 * 🌍 Système de Traduction Multilingue AdsClass
 * Support: Français (FR), English (EN), العربية (AR)
 * Version: 1.0.0
 */

class I18nManager {
    constructor() {
        this.currentLang = localStorage.getItem('adsclass_lang') || 'fr';
        this.translations = {};
        this.supportedLanguages = {
            'fr': { name: 'Français', flag: '🇫🇷', dir: 'ltr' },
            'en': { name: 'English', flag: '🇬🇧', dir: 'ltr' },
            'ar': { name: 'العربية', flag: '🇸🇦', dir: 'rtl' }
        };
    }

    /**
     * Initialiser le système de traduction
     */
    async init() {
        await this.loadTranslations(this.currentLang);
        this.applyLanguage(this.currentLang);
        this.setupLanguageSelector();
    }

    /**
     * Charger les traductions depuis le serveur
     */
    async loadTranslations(lang) {
        try {
            const response = await fetch(`/static/translations/${lang}.json`);
            if (!response.ok) {
                console.warn(`Traductions ${lang} non trouvées, utilisation du français par défaut`);
                if (lang !== 'fr') {
                    return await this.loadTranslations('fr');
                }
                return;
            }
            this.translations = await response.json();
            console.log(`✅ Traductions ${lang} chargées avec succès`);
        } catch (error) {
            console.error(`❌ Erreur chargement traductions ${lang}:`, error);
        }
    }

    /**
     * Appliquer une langue à toute l'application
     */
    async applyLanguage(lang) {
        if (!this.supportedLanguages[lang]) {
            console.error(`Langue ${lang} non supportée`);
            return;
        }

        // Charger les traductions si nécessaire
        if (this.currentLang !== lang) {
            await this.loadTranslations(lang);
        }

        this.currentLang = lang;
        localStorage.setItem('adsclass_lang', lang);

        // Appliquer la direction du texte (RTL pour l'arabe)
        const dir = this.supportedLanguages[lang].dir;
        document.documentElement.setAttribute('dir', dir);
        document.documentElement.setAttribute('lang', lang);

        // Ajouter/retirer la classe RTL
        if (dir === 'rtl') {
            document.body.classList.add('rtl');
        } else {
            document.body.classList.remove('rtl');
        }

        // Traduire tous les éléments
        this.translatePage();

        // Émettre un événement personnalisé
        window.dispatchEvent(new CustomEvent('languageChanged', { detail: { lang } }));

        console.log(`🌍 Langue changée: ${this.supportedLanguages[lang].name}`);
    }

    /**
     * Traduire tous les éléments de la page
     */
    translatePage() {
        // Traduire les éléments avec data-i18n
        document.querySelectorAll('[data-i18n]').forEach(element => {
            const key = element.getAttribute('data-i18n');
            const translation = this.getTranslation(key);
            if (translation) {
                element.textContent = translation;
            }
        });

        // Traduire les placeholders
        document.querySelectorAll('[data-i18n-placeholder]').forEach(element => {
            const key = element.getAttribute('data-i18n-placeholder');
            const translation = this.getTranslation(key);
            if (translation) {
                element.placeholder = translation;
            }
        });

        // Traduire les titres (title attribute)
        document.querySelectorAll('[data-i18n-title]').forEach(element => {
            const key = element.getAttribute('data-i18n-title');
            const translation = this.getTranslation(key);
            if (translation) {
                element.title = translation;
            }
        });

        // Traduire les valeurs (pour les boutons)
        document.querySelectorAll('[data-i18n-value]').forEach(element => {
            const key = element.getAttribute('data-i18n-value');
            const translation = this.getTranslation(key);
            if (translation) {
                element.value = translation;
            }
        });
    }

    /**
     * Obtenir une traduction par clé
     */
    getTranslation(key) {
        const keys = key.split('.');
        let value = this.translations;
        
        for (const k of keys) {
            if (value && typeof value === 'object' && k in value) {
                value = value[k];
            } else {
                console.warn(`Traduction manquante: ${key}`);
                return key; // Retourner la clé si traduction non trouvée
            }
        }
        
        return value;
    }

    /**
     * Traduire une clé directement (pour usage dans JS)
     */
    t(key, params = {}) {
        let translation = this.getTranslation(key);
        
        // Remplacer les paramètres {param}
        Object.keys(params).forEach(param => {
            translation = translation.replace(`{${param}}`, params[param]);
        });
        
        return translation;
    }

    /**
     * Configurer le sélecteur de langue
     */
    setupLanguageSelector() {
        const selector = document.getElementById('language-selector');
        if (!selector) return;

        // Mettre à jour l'affichage actuel
        this.updateLanguageDisplay();

        // Ajouter les événements de clic sur les options
        document.querySelectorAll('.lang-option').forEach(option => {
            option.addEventListener('click', (e) => {
                e.preventDefault();
                const lang = option.getAttribute('data-lang');
                this.applyLanguage(lang);
                this.updateLanguageDisplay();
            });
        });
    }

    /**
     * Mettre à jour l'affichage du sélecteur de langue
     */
    updateLanguageDisplay() {
        const currentBtn = document.getElementById('current-language');
        if (currentBtn) {
            const langInfo = this.supportedLanguages[this.currentLang];
            currentBtn.innerHTML = `
                <span class="text-xl">${langInfo.flag}</span>
                <span class="font-semibold">${langInfo.name}</span>
                <i class="fas fa-chevron-down ml-2"></i>
            `;
        }

        // Marquer l'option active
        document.querySelectorAll('.lang-option').forEach(option => {
            const lang = option.getAttribute('data-lang');
            if (lang === this.currentLang) {
                option.classList.add('bg-indigo-50', 'border-indigo-300');
            } else {
                option.classList.remove('bg-indigo-50', 'border-indigo-300');
            }
        });
    }

    /**
     * Obtenir la langue actuelle
     */
    getCurrentLanguage() {
        return this.currentLang;
    }

    /**
     * Obtenir toutes les langues supportées
     */
    getSupportedLanguages() {
        return this.supportedLanguages;
    }
}

// Instance globale
const i18n = new I18nManager();

// Initialiser au chargement de la page
document.addEventListener('DOMContentLoaded', () => {
    i18n.init();
});

// Exposer globalement pour usage dans les templates
window.i18n = i18n;

