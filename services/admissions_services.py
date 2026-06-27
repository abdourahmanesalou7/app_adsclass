"""
Services Admissions — WhatsApp Business API & Paiements en ligne
Stripe (cartes) + Flutterwave (Mobile Money Afrique)
"""

import os
import re
import json
import secrets
import hashlib
import hmac
import requests
from datetime import datetime

# === CONFIGURATION ===

INTEGRATIONS = {
    'whatsapp_token': os.environ.get('WHATSAPP_API_TOKEN', ''),
    'whatsapp_phone_id': os.environ.get('WHATSAPP_PHONE_NUMBER_ID', ''),
    'whatsapp_business_id': os.environ.get('WHATSAPP_BUSINESS_ACCOUNT_ID', ''),
    'whatsapp_api_version': os.environ.get('WHATSAPP_API_VERSION', 'v21.0'),
    'stripe_secret_key': os.environ.get('STRIPE_SECRET_KEY', ''),
    'stripe_webhook_secret': os.environ.get('STRIPE_WEBHOOK_SECRET', ''),
    'flutterwave_secret_key': os.environ.get('FLUTTERWAVE_SECRET_KEY', ''),
    'flutterwave_public_key': os.environ.get('FLUTTERWAVE_PUBLIC_KEY', ''),
    'flutterwave_webhook_secret': os.environ.get('FLUTTERWAVE_WEBHOOK_SECRET', ''),
    'app_base_url': os.environ.get('APP_BASE_URL', 'http://127.0.0.1:5000'),
    'admissions_fee_default': int(os.environ.get('ADMISSIONS_FEE_DEFAULT', '75000')),
}

WHATSAPP_TEMPLATES = {
    'candidature_recue': {
        'name': 'candidature_recue',
        'body': "Bonjour {prenom},\n\nVotre candidature AdsClass a été reçue.\nRéférence : {reference}\nProgramme : {programme}\n\nService Admissions — AdsClass",
    },
    'entretien_planifie': {
        'name': 'entretien_planifie',
        'body': "Bonjour {prenom},\n\nVotre entretien d'admission est planifié le {date} à {heure}.\nLieu : {lieu}\n\nMerci de confirmer votre présence.\nService Admissions — AdsClass",
    },
    'admis_felicitations': {
        'name': 'admis_felicitations',
        'body': "Félicitations {prenom} !\n\nVous êtes admis(e) à AdsClass pour {programme}.\nProcédez au paiement des frais d'inscription :\n{payment_link}\n\nService Admissions — AdsClass",
    },
    'paiement_confirme': {
        'name': 'paiement_confirme',
        'body': "Bonjour {prenom},\n\nVotre paiement de {montant} FCFA a été confirmé.\nRéf. : {reference}\n\nBienvenue à AdsClass !",
    },
    'relance_inscription': {
        'name': 'relance_inscription',
        'body': "Bonjour {prenom},\n\nNous avons hâte de vous accueillir à AdsClass.\nFinalisez votre inscription : {payment_link}\n\nService Admissions",
    },
}


def is_whatsapp_configured():
    return bool(INTEGRATIONS['whatsapp_token'] and INTEGRATIONS['whatsapp_phone_id'])


def is_stripe_configured():
    return bool(INTEGRATIONS['stripe_secret_key'])


def is_flutterwave_configured():
    return bool(INTEGRATIONS['flutterwave_secret_key'] and INTEGRATIONS['flutterwave_public_key'])


def format_phone_e164(phone, default_country='227'):
    """Formater un numéro pour WhatsApp (E.164 sans +)."""
    if not phone:
        return None
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) == 8 and default_country:
        digits = default_country + digits
    elif len(digits) == 9 and digits.startswith('0'):
        digits = default_country + digits[1:]
    return digits if len(digits) >= 10 else None


class WhatsAppService:
    """Meta WhatsApp Business Cloud API."""

    @staticmethod
    def _api_url():
        return f"https://graph.facebook.com/{INTEGRATIONS['whatsapp_api_version']}/{INTEGRATIONS['whatsapp_phone_id']}/messages"

    @staticmethod
    def send_text(to_phone, message):
        """Envoyer un message texte via WhatsApp Business API."""
        phone = format_phone_e164(to_phone)
        if not phone:
            return {'success': False, 'error': 'Numéro invalide', 'fallback': True}

        if not is_whatsapp_configured():
            return {
                'success': False,
                'error': 'WhatsApp Business API non configurée',
                'fallback': True,
                'wa_me_url': f"https://wa.me/{phone}?text={requests.utils.quote(message)}",
            }

        try:
            response = requests.post(
                WhatsAppService._api_url(),
                headers={
                    'Authorization': f"Bearer {INTEGRATIONS['whatsapp_token']}",
                    'Content-Type': 'application/json',
                },
                json={
                    'messaging_product': 'whatsapp',
                    'recipient_type': 'individual',
                    'to': phone,
                    'type': 'text',
                    'text': {'preview_url': True, 'body': message[:4096]},
                },
                timeout=30,
            )
            data = response.json()
            if response.status_code in (200, 201):
                msg_id = data.get('messages', [{}])[0].get('id', '')
                return {'success': True, 'message_id': msg_id, 'phone': phone, 'provider': 'whatsapp_business'}
            return {
                'success': False,
                'error': data.get('error', {}).get('message', response.text),
                'fallback': True,
                'wa_me_url': f"https://wa.me/{phone}?text={requests.utils.quote(message)}",
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'fallback': True,
                'wa_me_url': f"https://wa.me/{phone}?text={requests.utils.quote(message)}",
            }

    @staticmethod
    def send_template_message(to_phone, template_key, variables=None):
        """Envoyer via un modèle prédéfini (texte formaté)."""
        tpl = WHATSAPP_TEMPLATES.get(template_key)
        if not tpl:
            return {'success': False, 'error': f'Modèle {template_key} introuvable'}
        message = tpl['body'].format(**(variables or {}))
        return WhatsAppService.send_text(to_phone, message)

    @staticmethod
    def get_business_profile():
        """Vérifier la connexion API WhatsApp."""
        if not is_whatsapp_configured():
            return {'configured': False}
        try:
            url = f"https://graph.facebook.com/{INTEGRATIONS['whatsapp_api_version']}/{INTEGRATIONS['whatsapp_phone_id']}"
            r = requests.get(url, params={'fields': 'display_phone_number,verified_name'},
                             headers={'Authorization': f"Bearer {INTEGRATIONS['whatsapp_token']}"}, timeout=10)
            if r.status_code == 200:
                d = r.json()
                return {'configured': True, 'phone': d.get('display_phone_number'), 'name': d.get('verified_name')}
        except Exception as e:
            return {'configured': True, 'error': str(e)}
        return {'configured': True, 'error': 'Connexion échouée'}


class PaymentService:
    """Paiements en ligne — Stripe & Flutterwave."""

    @staticmethod
    def generate_payment_token():
        return secrets.token_urlsafe(32)

    @staticmethod
    def create_stripe_checkout(candidat, montant, payment_id, description=None):
        """Créer une session Stripe Checkout."""
        if not is_stripe_configured():
            return {'success': False, 'error': 'Stripe non configuré (STRIPE_SECRET_KEY)'}

        base = INTEGRATIONS['app_base_url'].rstrip('/')
        success_url = f"{base}/apply/pay/{candidat['payment_token']}/success?provider=stripe&session={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{base}/apply/pay/{candidat['payment_token']}"

        try:
            response = requests.post(
                'https://api.stripe.com/v1/checkout/sessions',
                auth=(INTEGRATIONS['stripe_secret_key'], ''),
                data={
                    'mode': 'payment',
                    'success_url': success_url,
                    'cancel_url': cancel_url,
                    'client_reference_id': str(payment_id),
                    'customer_email': candidat.get('email', ''),
                    'line_items[0][price_data][currency]': 'xof',
                    'line_items[0][price_data][unit_amount]': int(montant),
                    'line_items[0][price_data][product_data][name]': description or f"Frais d'inscription — {candidat.get('reference', '')}",
                    'line_items[0][price_data][product_data][description]': f"AdsClass — {candidat.get('prenom', '')} {candidat.get('nom', '')}",
                    'line_items[0][quantity]': '1',
                    'metadata[candidat_id]': str(candidat['id']),
                    'metadata[payment_id]': str(payment_id),
                    'metadata[reference]': candidat.get('reference', ''),
                },
                timeout=30,
            )
            data = response.json()
            if response.status_code in (200, 201):
                return {
                    'success': True,
                    'provider': 'stripe',
                    'session_id': data['id'],
                    'payment_url': data['url'],
                }
            return {'success': False, 'error': data.get('error', {}).get('message', response.text)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def create_flutterwave_payment(candidat, montant, payment_id, provider='mobilemoney'):
        """Initier un paiement Flutterwave (Orange Money, MTN, etc.)."""
        if not is_flutterwave_configured():
            return {'success': False, 'error': 'Flutterwave non configuré'}

        tx_ref = f"ADS-{candidat.get('reference', payment_id)}-{secrets.token_hex(4).upper()}"
        base = INTEGRATIONS['app_base_url'].rstrip('/')

        payload = {
            'tx_ref': tx_ref,
            'amount': montant,
            'currency': 'XOF',
            'redirect_url': f"{base}/apply/pay/{candidat['payment_token']}/success?provider=flutterwave&tx_ref={tx_ref}",
            'customer': {
                'email': candidat.get('email') or f"candidat{candidat['id']}@adsclass.ne",
                'phonenumber': candidat.get('telephone', ''),
                'name': f"{candidat.get('prenom', '')} {candidat.get('nom', '')}".strip(),
            },
            'customizations': {
                'title': 'AdsClass — Frais inscription',
                'description': f"Dossier {candidat.get('reference', '')}",
                'logo': f"{base}/static/img/logo.png",
            },
            'meta': {
                'candidat_id': candidat['id'],
                'payment_id': payment_id,
            },
        }

        if provider in ('orange_money', 'airtel_money', 'mobilemoney'):
            payload['payment_options'] = 'mobilemoney'

        try:
            response = requests.post(
                'https://api.flutterwave.com/v3/payments',
                headers={
                    'Authorization': f"Bearer {INTEGRATIONS['flutterwave_secret_key']}",
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=30,
            )
            data = response.json()
            if data.get('status') == 'success':
                return {
                    'success': True,
                    'provider': 'flutterwave',
                    'tx_ref': tx_ref,
                    'payment_url': data['data']['link'],
                    'flw_ref': data['data'].get('flw_ref', ''),
                }
            return {'success': False, 'error': data.get('message', 'Erreur Flutterwave')}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def verify_stripe_session(session_id):
        """Vérifier une session Stripe après paiement."""
        if not is_stripe_configured():
            return {'success': False, 'error': 'Stripe non configuré'}
        try:
            r = requests.get(
                f'https://api.stripe.com/v1/checkout/sessions/{session_id}',
                auth=(INTEGRATIONS['stripe_secret_key'], ''),
                params={'expand[]': 'payment_intent'},
                timeout=20,
            )
            data = r.json()
            if r.status_code == 200 and data.get('payment_status') == 'paid':
                return {
                    'success': True,
                    'paid': True,
                    'session_id': session_id,
                    'amount': data.get('amount_total', 0),
                    'reference': data.get('payment_intent', {}).get('id', session_id),
                    'metadata': data.get('metadata', {}),
                }
            return {'success': True, 'paid': False, 'status': data.get('payment_status')}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def verify_flutterwave_transaction(tx_ref):
        """Vérifier une transaction Flutterwave par tx_ref."""
        if not is_flutterwave_configured():
            return {'success': False, 'error': 'Flutterwave non configuré'}
        try:
            r = requests.get(
                f'https://api.flutterwave.com/v3/transactions/verify_by_reference?tx_ref={tx_ref}',
                headers={'Authorization': f"Bearer {INTEGRATIONS['flutterwave_secret_key']}"},
                timeout=20,
            )
            data = r.json()
            if data.get('status') == 'success' and data['data']['status'] == 'successful':
                return {
                    'success': True,
                    'paid': True,
                    'tx_ref': tx_ref,
                    'amount': data['data']['amount'],
                    'reference': data['data'].get('flw_ref', tx_ref),
                    'channel': data['data'].get('payment_type', 'mobilemoney'),
                    'metadata': data['data'].get('meta', {}),
                }
            return {'success': True, 'paid': False}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def verify_stripe_webhook(payload, sig_header):
        """Vérifier la signature webhook Stripe."""
        secret = INTEGRATIONS['stripe_webhook_secret']
        if not secret:
            return {'valid': False, 'error': 'STRIPE_WEBHOOK_SECRET non configuré'}
        try:
            elements = sig_header.split(',')
            timestamp = None
            signatures = []
            for el in elements:
                k, v = el.split('=', 1)
                if k == 't':
                    timestamp = v
                elif k == 'v1':
                    signatures.append(v)
            signed = f"{timestamp}.{payload.decode('utf-8') if isinstance(payload, bytes) else payload}"
            expected = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
            if any(hmac.compare_digest(expected, s) for s in signatures):
                return {'valid': True, 'event': json.loads(payload)}
            return {'valid': False, 'error': 'Signature invalide'}
        except Exception as e:
            return {'valid': False, 'error': str(e)}

    @staticmethod
    def get_integrations_status():
        """Statut des intégrations pour le dashboard admin."""
        wa = WhatsAppService.get_business_profile()
        return {
            'whatsapp': {
                'configured': is_whatsapp_configured(),
                'connected': wa.get('configured') and not wa.get('error'),
                'phone': wa.get('phone'),
                'name': wa.get('name'),
            },
            'stripe': {'configured': is_stripe_configured()},
            'flutterwave': {'configured': is_flutterwave_configured()},
        }
