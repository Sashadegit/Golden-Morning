"""Envoie le cours quotidien de l'once d'or en EUR sur Telegram.

Lancé une fois par jour (cron à 8h). Lit le token et le chat cible depuis .env :
  - TELEGRAM_BOT_TOKEN
  - GOLDEN_CHAT_ID

L'envoi est tenté plusieurs fois (retry) pour absorber un creux d'API à 8h pile.
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

HTTP_TIMEOUT = 10
MAX_TENTATIVES = 3        # nombre d'essais d'envoi
DELAI_RETRY = 30          # secondes entre deux essais


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


def send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["GOLDEN_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    logger.info("Message envoyé au chat %s", chat_id)


def main() -> None:
    _load_env()
    derniere_exc: Exception | None = None
    for tentative in range(1, MAX_TENTATIVES + 1):
        try:
            send(build_message())
            return
        except Exception as exc:  # noqa: BLE001 - on retente
            derniere_exc = exc
            logger.warning(
                "Tentative %d/%d échouée (%s)", tentative, MAX_TENTATIVES, exc
            )
            if tentative < MAX_TENTATIVES:
                time.sleep(DELAI_RETRY)
    logger.error(
        "Envoi du cours de l'or définitivement échoué après %d tentatives : %s",
        MAX_TENTATIVES, derniere_exc,
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
