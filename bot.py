"""Golden Morning — bot interactif Telegram (long polling, sans dépendance externe).

Répond aux commandes :
  /start   message d'accueil
  /or      cours complet (once, gramme, lingot 1 kg, Napoléon)
  /lingot  prix du lingot 1 kg
  /gramme  prix du gramme
  /test    vérifie que le bot est en ligne

L'envoi automatique de 8h est géré séparément par send_gold.py (cron).
"""
from __future__ import annotations

import logging
import os
import time

import requests

from gold_service import get_gold_quote
from send_gold import _eur, _load_env, build_message, ONCE_EN_GRAMMES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("golden-morning-bot")

POLL_TIMEOUT = 30          # long polling Telegram
HTTP_TIMEOUT = POLL_TIMEOUT + 10

COMMANDS = [
    {"command": "or", "description": "Cours complet de l'or en EUR"},
    {"command": "lingot", "description": "Prix du lingot 1 kg"},
    {"command": "gramme", "description": "Prix du gramme d'or"},
    {"command": "test", "description": "Vérifier que le bot répond"},
]


def api(token: str, method: str, **params):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        data=params,
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def handle(text: str) -> str:
    cmd = (text or "").strip().split()[0].lower() if (text or "").strip() else ""
    cmd = cmd.split("@")[0]  # enlève @GoldenMorningBot dans les groupes

    if cmd == "/start":
        return (
            "🌅 *Bienvenue sur Golden Morning !*\n\n"
            "Je t'envoie le cours de l'or chaque matin à *8h00*.\n\n"
            "Commandes dispo à tout moment :\n"
            "• /or — le cours complet\n"
            "• /lingot — le lingot 1 kg\n"
            "• /gramme — le gramme\n"
            "• /test — vérifier que je réponds"
        )
    if cmd in ("/or", "/cours"):
        return build_message()
    if cmd == "/lingot":
        q = get_gold_quote()
        prix = q.price_eur / ONCE_EN_GRAMMES * 1000
        return f"🟨 *Lingot d'or 1 kg* : *{_eur(prix, 0)} €*\n_Source : {q.source}_"
    if cmd == "/gramme":
        q = get_gold_quote()
        prix = q.price_eur / ONCE_EN_GRAMMES
        return f"⚖️ *Le gramme d'or* : *{_eur(prix)} €*\n_Source : {q.source}_"
    if cmd == "/test":
        return "✅ Golden Morning est en ligne et opérationnel. Tape /or pour le cours."
    return (
        "Je connais : /or, /lingot, /gramme, /test.\n"
        "Tape /start pour le menu."
    )


def main() -> None:
    _load_env()
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    try:
        api(token, "setMyCommands", commands=__import__("json").dumps(COMMANDS))
    except Exception as exc:  # noqa: BLE001
        logger.warning("setMyCommands échoué (%s)", exc)

    offset = None
    logger.info("Bot Golden Morning démarré, en écoute…")
    while True:
        try:
            params = {"timeout": POLL_TIMEOUT}
            if offset is not None:
                params["offset"] = offset
            data = api(token, "getUpdates", **params)
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                reply = handle(msg.get("text", ""))
                api(token, "sendMessage", chat_id=chat_id, text=reply,
                    parse_mode="Markdown")
                logger.info("Réponse envoyée à %s", chat_id)
        except Exception as exc:  # noqa: BLE001 - on garde le bot en vie
            logger.warning("Erreur boucle (%s), nouvelle tentative dans 5 s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
