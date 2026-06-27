"""
AdsClass — Notifications Email, WhatsApp et messages métier
"""

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

try:
    from services.admissions_services import WhatsAppService, format_phone_e164
except ImportError:
    WhatsAppService = None
    format_phone_e164 = None

NOTIF_CONFIG = {
    'smtp_host': os.environ.get('SMTP_HOST', 'smtp.gmail.com'),
    'smtp_port': int(os.environ.get('SMTP_PORT', '587')),
    'smtp_user': os.environ.get('SMTP_USER', ''),
    'smtp_password': os.environ.get('SMTP_PASSWORD', ''),
    'smtp_from': os.environ.get('SMTP_FROM', os.environ.get('SMTP_USER', 'noreply@adsclass.com')),
    'smtp_use_tls': os.environ.get('SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes'),
    'app_name': os.environ.get('APP_NAME', 'AdsClass'),
    'app_base_url': os.environ.get('APP_BASE_URL', 'http://127.0.0.1:5000').rstrip('/'),
}


def is_email_configured():
    return bool(NOTIF_CONFIG['smtp_user'] and NOTIF_CONFIG['smtp_password'])


class EmailService:
    @staticmethod
    def send(to_email, subject, html_body, text_body=None):
        if not to_email:
            return {'success': False, 'error': 'Email destinataire manquant'}
        if not is_email_configured():
            print(f"📧 [Email simulé] À: {to_email} | {subject}")
            return {'success': False, 'error': 'SMTP non configuré', 'simulated': True}

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{NOTIF_CONFIG['app_name']} <{NOTIF_CONFIG['smtp_from']}>"
        msg['To'] = to_email
        plain = text_body or _html_to_plain(html_body)
        msg.attach(MIMEText(plain, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(NOTIF_CONFIG['smtp_host'], NOTIF_CONFIG['smtp_port'], timeout=30) as server:
                if NOTIF_CONFIG['smtp_use_tls']:
                    server.starttls(context=context)
                server.login(NOTIF_CONFIG['smtp_user'], NOTIF_CONFIG['smtp_password'])
                server.sendmail(NOTIF_CONFIG['smtp_from'], to_email, msg.as_string())
            return {'success': True, 'to': to_email}
        except Exception as e:
            print(f"Erreur email vers {to_email}: {e}")
            return {'success': False, 'error': str(e)}


def _html_to_plain(html):
    import re
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def _email_wrapper(title, body_html, cta_url=None, cta_label=None):
    cta = ''
    if cta_url and cta_label:
        cta = f'''
        <p style="margin-top:24px;text-align:center">
          <a href="{cta_url}" style="background:#4f46e5;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">{cta_label}</a>
        </p>'''
    return f'''<!DOCTYPE html><html><body style="font-family:Segoe UI,Arial,sans-serif;background:#f3f4f6;padding:24px">
    <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)">
      <div style="background:linear-gradient(135deg,#4f46e5,#7c3aed);padding:24px;color:#fff">
        <h1 style="margin:0;font-size:20px">{NOTIF_CONFIG['app_name']}</h1>
        <p style="margin:8px 0 0;opacity:.9;font-size:14px">{title}</p>
      </div>
      <div style="padding:24px;color:#374151;font-size:15px;line-height:1.6">{body_html}{cta}</div>
      <div style="padding:16px 24px;background:#f9fafb;font-size:12px;color:#9ca3af;text-align:center">
        Message automatique — {NOTIF_CONFIG['app_name']} · {datetime.now().strftime('%d/%m/%Y %H:%M')}
      </div>
    </div></body></html>'''


def _send_whatsapp(phone, message):
    if not phone or not WhatsAppService:
        return {'success': False, 'error': 'WhatsApp non disponible'}
    return WhatsAppService.send_text(phone, message)


def _format_date_fr(date_str):
    try:
        d = datetime.strptime(str(date_str)[:10], '%Y-%m-%d')
        return d.strftime('%d/%m/%Y')
    except ValueError:
        return str(date_str)


def notify_absence_marked(student, course, date_cours, statut='absent', prof_name=''):
    """Notifier l'étudiant d'une absence ou retard enregistré."""
    if statut not in ('absent', 'retard'):
        return {'skipped': True}

    prenom = student.get('prenom', '')
    nom = student.get('nom', '')
    cours = course.get('nom_cours', 'Cours')
    filiere = course.get('filiere', '')
    niveau = course.get('niveau', '')
    salle = course.get('salle', '')
    date_fr = _format_date_fr(date_cours)
    label = 'Absence' if statut == 'absent' else 'Retard'
    horaire = ''
    if course.get('heure_debut') and course.get('heure_fin'):
        horaire = f"{course['heure_debut']} - {course['heure_fin']}"

    wa_msg = (
        f"📋 *{NOTIF_CONFIG['app_name']} — {label} enregistré*\n\n"
        f"Bonjour {prenom},\n\n"
        f"Un {label.lower()} a été enregistré pour le cours :\n"
        f"• *{cours}*\n"
        f"• Date : {date_fr}\n"
    )
    if horaire:
        wa_msg += f"• Horaire : {horaire}\n"
    if salle:
        wa_msg += f"• Salle : {salle}\n"
    wa_msg += f"• Filière : {filiere}"
    if niveau:
        wa_msg += f" / {niveau}"
    wa_msg += f"\n• Professeur : {prof_name or '—'}\n\n"
    wa_msg += "Consultez votre espace étudiant pour plus de détails."

    body = f'''
    <p>Bonjour <strong>{prenom} {nom}</strong>,</p>
    <p>Un <strong>{label.lower()}</strong> a été enregistré pour votre cours :</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;border-bottom:1px solid #e5e7eb;color:#6b7280">Cours</td><td style="padding:8px;border-bottom:1px solid #e5e7eb"><strong>{cours}</strong></td></tr>
      <tr><td style="padding:8px;border-bottom:1px solid #e5e7eb;color:#6b7280">Date</td><td style="padding:8px;border-bottom:1px solid #e5e7eb">{date_fr}</td></tr>
      {"<tr><td style='padding:8px;border-bottom:1px solid #e5e7eb;color:#6b7280'>Horaire</td><td style='padding:8px;border-bottom:1px solid #e5e7eb'>" + horaire + "</td></tr>" if horaire else ""}
      {"<tr><td style='padding:8px;border-bottom:1px solid #e5e7eb;color:#6b7280'>Salle</td><td style='padding:8px;border-bottom:1px solid #e5e7eb'>" + salle + "</td></tr>" if salle else ""}
      <tr><td style="padding:8px;border-bottom:1px solid #e5e7eb;color:#6b7280">Professeur</td><td style="padding:8px;border-bottom:1px solid #e5e7eb">{prof_name or '—'}</td></tr>
    </table>
    <p style="color:#dc2626;font-size:14px">⚠️ Si vous pensez qu'il s'agit d'une erreur, contactez votre professeur ou la scolarité.</p>
    '''

    dashboard_url = f"{NOTIF_CONFIG['app_base_url']}/student/absences"
    html = _email_wrapper(f"{label} enregistré", body, dashboard_url, 'Voir mes absences')
    subject = f"[{NOTIF_CONFIG['app_name']}] {label} — {cours} ({date_fr})"

    results = {'email': None, 'whatsapp': None}
    if student.get('email'):
        results['email'] = EmailService.send(student['email'], subject, html)
    if student.get('telephone'):
        results['whatsapp'] = _send_whatsapp(student['telephone'], wa_msg)
    return results


def notify_admin_absence_alert(admin, student, course, date_cours, statut='absent'):
    """Alerter un administrateur d'une nouvelle absence."""
    label = 'Absence' if statut == 'absent' else 'Retard'
    date_fr = _format_date_fr(date_cours)
    cours = course.get('nom_cours', 'Cours')

    body = f'''
    <p>Nouvelle <strong>{label.lower()}</strong> enregistrée :</p>
    <ul>
      <li><strong>Étudiant :</strong> {student.get('prenom')} {student.get('nom')} ({student.get('filiere')} / {student.get('niveau', '')})</li>
      <li><strong>Cours :</strong> {cours}</li>
      <li><strong>Date :</strong> {date_fr}</li>
    </ul>
    '''
    url = f"{NOTIF_CONFIG['app_base_url']}/admin/absences"
    html = _email_wrapper(f"Alerte {label}", body, url, 'Voir les absences')
    subject = f"[{NOTIF_CONFIG['app_name']}] {label} — {student.get('prenom')} {student.get('nom')}"

    wa_msg = (
        f"🔔 *{NOTIF_CONFIG['app_name']} — Alerte {label}*\n\n"
        f"Étudiant : {student.get('prenom')} {student.get('nom')}\n"
        f"Cours : {cours}\nDate : {date_fr}\n"
        f"Filière : {student.get('filiere', '')}"
    )

    results = {}
    if admin.get('email'):
        results['email'] = EmailService.send(admin['email'], subject, html)
    if admin.get('telephone'):
        results['whatsapp'] = _send_whatsapp(admin['telephone'], wa_msg)
    return results


def notify_course_cancelled(recipient, course, role='etudiant', motif=''):
    """Notifier étudiant ou professeur de l'annulation d'un cours."""
    prenom = recipient.get('prenom', '')
    cours = course.get('nom_cours', 'Cours')
    filiere = course.get('filiere', '')
    niveau = course.get('niveau', '')
    salle = course.get('salle', '')
    jour = course.get('jour_semaine', '')
    hd = course.get('heure_debut', '')
    hf = course.get('heure_fin', '')
    horaire = f"{jour} {hd}-{hf}".strip() if jour or hd else ''

    role_label = 'étudiant' if role == 'etudiant' else 'professeur'
    motif_txt = f"\nMotif : {motif}" if motif else ''

    wa_msg = (
        f"🚫 *{NOTIF_CONFIG['app_name']} — Cours annulé*\n\n"
        f"Bonjour {prenom},\n\n"
        f"Le cours suivant a été *annulé* par l'administration :\n"
        f"• *{cours}*\n"
    )
    if horaire:
        wa_msg += f"• Créneau : {horaire}\n"
    if salle:
        wa_msg += f"• Salle : {salle}\n"
    wa_msg += f"• Filière : {filiere}"
    if niveau:
        wa_msg += f" / {niveau}"
    wa_msg += motif_txt + "\n\nConsultez votre emploi du temps mis à jour."

    body = f'''
    <p>Bonjour <strong>{prenom} {recipient.get('nom', '')}</strong>,</p>
    <p>Le cours suivant a été <strong style="color:#dc2626">annulé</strong> par l'administration :</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;border-bottom:1px solid #e5e7eb;color:#6b7280">Cours</td><td style="padding:8px;border-bottom:1px solid #e5e7eb"><strong>{cours}</strong></td></tr>
      {"<tr><td style='padding:8px;border-bottom:1px solid #e5e7eb;color:#6b7280'>Créneau</td><td style='padding:8px;border-bottom:1px solid #e5e7eb'>" + horaire + "</td></tr>" if horaire else ""}
      {"<tr><td style='padding:8px;border-bottom:1px solid #e5e7eb;color:#6b7280'>Salle</td><td style='padding:8px;border-bottom:1px solid #e5e7eb'>" + salle + "</td></tr>" if salle else ""}
      <tr><td style="padding:8px;color:#6b7280">Filière / Niveau</td><td style="padding:8px">{filiere} {('/ ' + niveau) if niveau else ''}</td></tr>
    </table>
    {"<p><em>Motif : " + motif + "</em></p>" if motif else ""}
  <p>Votre emploi du temps a été mis à jour automatiquement.</p>
    '''

    dash_url = (
        f"{NOTIF_CONFIG['app_base_url']}/student/dashboard"
        if role == 'etudiant'
        else f"{NOTIF_CONFIG['app_base_url']}/professeur/emploi-temps"
    )
    html = _email_wrapper('Cours annulé', body, dash_url, 'Voir mon emploi du temps')
    subject = f"[{NOTIF_CONFIG['app_name']}] Annulation — {cours}"

    results = {}
    if recipient.get('email'):
        results['email'] = EmailService.send(recipient['email'], subject, html)
    if recipient.get('telephone'):
        results['whatsapp'] = _send_whatsapp(recipient['telephone'], wa_msg)
    return results
