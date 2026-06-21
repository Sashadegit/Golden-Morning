"""Récupération du cours du Bitcoin en EUR, avec fallback multi-sources.

Sources (gratuites, sans clé) :
  - Primaire : CoinGecko (bitcoin, EUR) -> prix + variation 24h directs.
  - Fallback : Kraken (XBTEUR) -> dernier prix + variation depuis l'ouverture du jour.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 10  # secondes


@dataclass
class BtcQuote:
    price_eur: float          # prix d'1 BTC en EUR
    change_pct: float | None  # variation jour en %, si fournie par la source
    source: str               # nom de la source utilisée
    fetched_at: datetime      # horodatage UTC de la récupération


def _fetch_primary() -> BtcQuote:
    """CoinGecko : prix EUR + variation 24h directs."""
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": "bitcoin",
            "vs_currencies": "eur",
            "include_24hr_change": "true",
        },
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()["bitcoin"]
    return BtcQuote(
        price_eur=round(float(data["eur"]), 2),
        change_pct=round(float(data["eur_24h_change"]), 2),
        source="CoinGecko",
        fetched_at=datetime.now(timezone.utc),
    )


def _fetch_fallback() -> BtcQuote:
    """Kraken (XBTEUR) : dernier prix + variation depuis l'ouverture du jour."""
    r = requests.get(
        "https://api.kraken.com/0/public/Ticker",
        params={"pair": "XBTEUR"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise RuntimeError(f"Kraken a renvoyé une erreur : {payload['error']}")
    result = payload["result"]
    cle = next(iter(result))            # ex : "XXBTZEUR"
    ticker = result[cle]
    dernier = float(ticker["c"][0])     # dernier prix échangé
    ouverture = float(ticker["o"])      # prix d'ouverture du jour
    change = round((dernier - ouverture) / ouverture * 100, 2) if ouverture else None
    return BtcQuote(
        price_eur=round(dernier, 2),
        change_pct=change,
        source="Kraken",
        fetched_at=datetime.now(timezone.utc),
    )


def get_btc_quote() -> BtcQuote:
    """Cours du Bitcoin en EUR : source primaire puis fallback automatique."""
    try:
        quote = _fetch_primary()
        logger.info("Cours BTC via %s : %.2f EUR", quote.source, quote.price_eur)
        return quote
    except Exception as exc:  # noqa: BLE001 - fallback global
        logger.warning("Source primaire BTC KO (%s), passage au fallback", exc)
        quote = _fetch_fallback()
        logger.info("Cours BTC via %s : %.2f EUR", quote.source, quote.price_eur)
        return quote
