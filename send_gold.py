"""Envoie le cours quotidien de l'once d'or en EUR (Telegram et/ou WhatsApp).

Lancé une fois par jour (cron à 8h). Lit la config depuis .env :
  - TELEGRAM_BOT_TOKEN + GOLDEN_CHAT_ID  -> envoi Telegram (si présents)
  - WhatsApp via CallMeBot (gratuit), un ou plusieurs destinataires :
      * CALLMEBOT_RECIPIENTS="+33xxx:cle1,+33yyy:cle2"   (plusieurs)
      * ou CALLMEBOT_PHONE + CALLMEBOT_APIKEY            (un seul)

Chaque destinataire/canal configuré reçoit le message, avec retry. Un canal qui
échoue n'empêche pas les autres. Le formatage *gras* / _italique_ est compris
aussi bien par Telegram (Markdown) que par WhatsApp.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests

from gold_service import get_gold_quote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("golden-morning")

HTTP_TIMEOUT = 10          # Telegram
HTTP_TIMEOUT_WA = 30       # CallMeBot peut être lent (file d'attente)
MAX_TENTATIVES = 3         # nombre d'essais d'envoi par canal
DELAI_RETRY = 30           # secondes entre deux essais


def _load_env() -> None:
    """Charge les variables depuis .env (sans dépendance externe)."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
ONCE_EN_GRAMMES = 31.1034768   # once troy
NAPOLEON_GR_OR_FIN = 5.806     # pièce 20 francs : or fin contenu


def _eur(montant: float, decimales: int = 2) -> str:
    """Formate un montant à la française : 3 745,14"""
    s = f"{montant:,.{decimales}f}"
    return s.replace(",", " ").replace(".", ",")


def build_message() -> str:
    q = get_gold_quote()
    now = q.fetched_at.astimezone()
    date_fr = f"{now.day} {MOIS_FR[now.month - 1]} {now.year}"

    prix_gramme = q.price_eur / ONCE_EN_GRAMMES
    prix_lingot = prix_gramme * 1000          # lingot standard 1 kg
    prix_napoleon = prix_gramme * NAPOLEON_GR_OR_FIN

    lignes = [
        "🌅 *GOLDEN MORNING* 🌅",
        f"_{date_fr}_",
        "━━━━━━━━━━━━━━━",
        f"🥇 Once d'or (31,1 g) : *{_eur(q.price_eur)} €*",
        f"⚖️ Le gramme : *{_eur(prix_gramme)} €*",
        f"🟨 Lingot 1 kg : *{_eur(prix_lingot, 0)} €*",
        f"🪙 Napoléon 20F : *{_eur(prix_napoleon)} €*",
    ]
    if q.change_pct is not None:
        fleche = "📈" if q.change_pct >= 0 else "📉"
        humeur = "Belle journée dorée ✨" if q.change_pct >= 0 else "Repli ce matin ☕"
        lignes.append("━━━━━━━━━━━━━━━")
        lignes.append(f"{fleche} Variation 24h : *{q.change_pct:+.2f} %* — {humeur}")
    lignes.append(f"\n_Source : {q.source}_")
    return "\n".join(lignes)


def send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["GOLDEN_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    logger.info("Message Telegram envoyé au chat %s", chat_id)


def send_whatsapp(text: str, phone: str, apikey: str) -> None:
    """Envoi WhatsApp via CallMeBot (gratuit). Le numéro doit avoir activé la clé."""
    r = requests.get(
        "https://api.callmebot.com/whatsapp.php",
        params={"phone": phone, "text": text, "apikey": apikey},
        timeout=HTTP_TIMEOUT_WA,
    )
    r.raise_for_status()
    corps = r.text.lower()
    if "error" in corps or ("apikey" in corps and "not" in corps):
        raise RuntimeError(f"CallMeBot a refusé l'envoi : {r.text[:200]}")
    logger.info("Message WhatsApp envoyé à %s (CallMeBot)", phone)


def _destinataires_whatsapp():
    """Liste (phone, apikey) depuis le .env, dédoublonnée par numéro.

    Accepte CALLMEBOT_RECIPIENTS="+33xxx:cle1,+33yyy:cle2" et/ou le couple
    simple CALLMEBOT_PHONE + CALLMEBOT_APIKEY.
    """
    rec = []
    bulk = os.environ.get("CALLMEBOT_RECIPIENTS", "").strip()
    if bulk:
        for item in bulk.split(","):
            item = item.strip()
            if not item or ":" not in item:
                continue
            phone, _, cle = item.partition(":")
            rec.append((phone.strip(), cle.strip()))
    phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
    cle = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if phone and cle:
        rec.append((phone, cle))
    dedup = {}
    for p, k in rec:
        if p and k:
            dedup[p] = k
    return list(dedup.items())


def _canaux():
    """Liste des canaux configurés : (nom, fonction d'envoi prenant le texte)."""
    canaux = []
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("GOLDEN_CHAT_ID"):
        canaux.append(("Telegram", send_telegram))
    for phone, cle in _destinataires_whatsapp():
        canaux.append(
            (f"WhatsApp {phone}",
             lambda text, p=phone, k=cle: send_whatsapp(text, p, k))
        )
    return canaux


def _envoyer_avec_retry(nom: str, fonction, text: str) -> bool:
    """Tente l'envoi sur un canal, avec retry. True si réussi."""
    for tentative in range(1, MAX_TENTATIVES + 1):
        try:
            fonction(text)
            return True
        except Exception as exc:  # noqa: BLE001 - on retente
            logger.warning(
                "[%s] tentative %d/%d échouée (%s)",
                nom, tentative, MAX_TENTATIVES, exc,
            )
            if tentative < MAX_TENTATIVES:
                time.sleep(DELAI_RETRY)
    logger.error("[%s] envoi définitivement échoué", nom)
    return False


def main() -> None:
    _load_env()
    canaux = _canaux()
    if not canaux:
        logger.error("Aucun canal configuré (ni Telegram ni WhatsApp) dans .env")
        raise SystemExit(1)

    text = build_message()
    resultats = {nom: _envoyer_avec_retry(nom, fn, text) for nom, fn in canaux}

    if not any(resultats.values()):
        logger.error("Échec sur TOUS les canaux : %s", resultats)
        raise SystemExit(1)
    logger.info("Bilan envoi : %s", resultats)


if __name__ == "__main__":
    main()
