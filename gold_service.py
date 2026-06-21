"""Récupération du cours de l'once d'or en EUR, avec fallback multi-sources.

Sources (toutes gratuites, sans clé) :
  - Primaire : gold-api.com (XAU/USD) converti en EUR via Frankfurter (BCE),
               avec open.er-api.com en secours pour le taux de change.
               La variation 24h est complétée en best-effort via CoinGecko PAXG.
  - Fallback : CoinGecko PAX Gold (PAXG ~ 1 once d'or), prix EUR + variation 24h.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 10  # secondes


@dataclass
class GoldQuote:
    price_eur: float          # prix de l'once en EUR
    change_pct: float | None  # variation jour en %, si fournie par la source
    source: str               # nom de la source utilisée
    fetched_at: datetime      # horodatage UTC de la récupération


def _usd_to_eur() -> float:
    """Taux USD -> EUR : Frankfurter (BCE) puis open.er-api.com en secours."""
    try:
        r = requests.get(
            "https://api.frankfurter.dev/v1/latest",
            params={"base": "USD", "symbols": "EUR"},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return float(r.json()["rates"]["EUR"])
    except Exception as exc:  # noqa: BLE001 - on bascule sur le secours
        logger.warning("Frankfurter indisponible (%s), bascule open.er-api.com", exc)
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return float(r.json()["rates"]["EUR"])


def _fetch_change_24h() -> float | None:
    """Variation 24h de l'or (%), best-effort via CoinGecko PAXG.

    Sert à enrichir la source primaire, qui ne fournit pas la variation.
    Renvoie None si indisponible (on n'échoue jamais pour ça).
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "pax-gold",
                "vs_currencies": "eur",
                "include_24hr_change": "true",
            },
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return round(float(r.json()["pax-gold"]["eur_24h_change"]), 2)
    except Exception as exc:  # noqa: BLE001 - simple enrichissement
        logger.warning("Variation 24h indisponible (%s)", exc)
        return None


def _fetch_primary() -> GoldQuote:
    """gold-api.com (XAU/USD sans clé) converti en EUR, variation via CoinGecko."""
    r = requests.get("https://api.gold-api.com/price/XAU", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    usd = float(r.json()["price"])
    eur_rate = _usd_to_eur()
    return GoldQuote(
        price_eur=round(usd * eur_rate, 2),
        change_pct=_fetch_change_24h(),
        source="gold-api.com + Frankfurter",
        fetched_at=datetime.now(timezone.utc),
    )


def _fetch_fallback() -> GoldQuote:
    """CoinGecko PAX Gold (PAXG ~ 1 once d'or) : prix EUR + variation 24h directs."""
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": "pax-gold",
            "vs_currencies": "eur",
            "include_24hr_change": "true",
        },
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()["pax-gold"]
    return GoldQuote(
        price_eur=round(float(data["eur"]), 2),
        change_pct=round(float(data["eur_24h_change"]), 2),
        source="CoinGecko PAXG",
        fetched_at=datetime.now(timezone.utc),
    )


def get_gold_quote() -> GoldQuote:
    """Cours de l'once d'or en EUR : source primaire puis fallback automatique."""
    try:
        quote = _fetch_primary()
        logger.info("Cours or via %s : %.2f EUR", quote.source, quote.price_eur)
        return quote
    except Exception as exc:  # noqa: BLE001 - fallback global
        logger.warning("Source primaire KO (%s), passage au fallback", exc)
        quote = _fetch_fallback()
        logger.info("Cours or via %s : %.2f EUR", quote.source, quote.price_eur)
        return quote
