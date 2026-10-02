"""Configuração operacional central do Radar de Ofertas.

Segredos continuam no .env. Este módulo concentra nomes, defaults, limites e
normalização para evitar divergência entre monitor, publicador e gate.
"""
from dataclasses import dataclass
import os


RADAR_INTERVAL_SECONDS = 1200
OFFER_MAX_AGE_MINUTES = 120
RECOVERED_MAX_AGE_MINUTES = 45
RECOVERED_INTERVAL_SECONDS = 30
MONITOR_RECOVERY_MINUTES = 30
MONITOR_RECOVERY_LIMIT = 500
GATE_SOURCE_PRICE_MAX_AGE_SECONDS = 300
GATE_KABUM_FEED_MAX_AGE_SECONDS = 1800


def env_text(name, default=""):
    value = os.getenv(name)
    return str(default if value is None else value).strip()


def env_int(name, default, *, minimum=None, maximum=None):
    try:
        value = int(env_text(name, default))
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def env_float(name, default, *, minimum=None, maximum=None):
    try:
        value = float(env_text(name, default))
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return value


def env_bool(name, default=False):
    raw = env_text(name, "1" if default else "0").lower()
    if raw in {"1", "true", "yes", "sim", "on"}:
        return True
    if raw in {"0", "false", "no", "nao", "não", "off", ""}:
        return False
    return bool(default)


def env_csv(name, *, lower=False):
    values = []
    for raw in env_text(name).split(","):
        value = raw.strip()
        if not value:
            continue
        values.append(value.lower() if lower else value)
    return tuple(values)


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    channel: str
    admin_chat: str
    api_id: str
    api_hash: str

    @classmethod
    def from_env(cls):
        return cls(
            token=env_text("TELEGRAM_TOKEN"),
            channel=env_text("TELEGRAM_CANAL"),
            admin_chat=env_text("TELEGRAM_ADMIN_CHAT"),
            api_id=env_text("TG_API_ID"),
            api_hash=env_text("TG_API_HASH"),
        )


@dataclass(frozen=True)
class PublisherConfig:
    require_image: bool
    radar_interval_seconds: int
    recovered_interval_seconds: int
    recovered_max_age_minutes: int
    offer_max_age_minutes: int

    @classmethod
    def from_env(cls):
        offer_max_age = env_int(
            "IDADE_MAXIMA_MINUTOS",
            OFFER_MAX_AGE_MINUTES,
            minimum=1,
            maximum=1440,
        )
        recovered_max_age = env_int(
            "IDADE_MAXIMA_RECUPERADAS_MINUTOS",
            RECOVERED_MAX_AGE_MINUTES,
            minimum=1,
            maximum=1440,
        )
        return cls(
            require_image=env_bool("EXIGIR_IMAGEM", True),
            radar_interval_seconds=env_int(
                "RADAR_INTERVALO_PUBLICACAO",
                RADAR_INTERVAL_SECONDS,
                minimum=60,
                maximum=86400,
            ),
            recovered_interval_seconds=env_int(
                "INTERVALO_RECUPERADAS",
                RECOVERED_INTERVAL_SECONDS,
                minimum=5,
                maximum=3600,
            ),
            recovered_max_age_minutes=min(offer_max_age, recovered_max_age),
            offer_max_age_minutes=offer_max_age,
        )


@dataclass(frozen=True)
class MonitorConfig:
    recovery_minutes: int
    recovery_limit: int
    offer_max_age_minutes: int
    recovered_max_age_minutes: int
    chats: tuple
    media_chats: tuple
    paused_chats: tuple

    @classmethod
    def from_env(cls):
        offer_max_age = env_int(
            "IDADE_MAXIMA_MINUTOS",
            OFFER_MAX_AGE_MINUTES,
            minimum=1,
            maximum=1440,
        )
        recovered_max_age = env_int(
            "IDADE_MAXIMA_RECUPERADAS_MINUTOS",
            RECOVERED_MAX_AGE_MINUTES,
            minimum=1,
            maximum=1440,
        )
        requested_recovery = env_int(
            "TG_RECUPERAR_MINUTOS",
            MONITOR_RECOVERY_MINUTES,
            minimum=0,
            maximum=1440,
        )
        return cls(
            recovery_minutes=min(requested_recovery, offer_max_age),
            recovery_limit=env_int(
                "TG_RECUPERAR_MAX_MENSAGENS",
                MONITOR_RECOVERY_LIMIT,
                minimum=20,
                maximum=2000,
            ),
            offer_max_age_minutes=offer_max_age,
            recovered_max_age_minutes=min(offer_max_age, recovered_max_age),
            chats=env_csv("TG_CHATS"),
            media_chats=env_csv("TG_MEDIA_CHATS"),
            paused_chats=env_csv("TG_PAUSED_CHATS", lower=True),
        )


@dataclass(frozen=True)
class GateConfig:
    source_price_max_age_seconds: int
    kabum_feed_max_age_seconds: int

    @classmethod
    def from_env(cls):
        return cls(
            source_price_max_age_seconds=env_int(
                "GATE_SOURCE_PRICE_MAX_AGE_SECONDS",
                GATE_SOURCE_PRICE_MAX_AGE_SECONDS,
                minimum=60,
                maximum=86400,
            ),
            kabum_feed_max_age_seconds=env_int(
                "GATE_KABUM_FEED_MAX_AGE_SECONDS",
                GATE_KABUM_FEED_MAX_AGE_SECONDS,
                minimum=60,
                maximum=86400,
            ),
        )


def operational_snapshot():
    """Resumo não sensível para diagnóstico e documentação."""
    publisher = PublisherConfig.from_env()
    monitor = MonitorConfig.from_env()
    gate = GateConfig.from_env()
    telegram = TelegramConfig.from_env()
    return {
        "publisher": {
            "require_image": publisher.require_image,
            "radar_interval_seconds": publisher.radar_interval_seconds,
            "recovered_interval_seconds": publisher.recovered_interval_seconds,
            "recovered_max_age_minutes": publisher.recovered_max_age_minutes,
            "offer_max_age_minutes": publisher.offer_max_age_minutes,
        },
        "monitor": {
            "recovery_minutes": monitor.recovery_minutes,
            "recovery_limit": monitor.recovery_limit,
            "configured_chats": len(monitor.chats),
            "media_chats": len(monitor.media_chats),
            "paused_chats": len(monitor.paused_chats),
        },
        "gate": {
            "source_price_max_age_seconds": gate.source_price_max_age_seconds,
            "kabum_feed_max_age_seconds": gate.kabum_feed_max_age_seconds,
        },
        "telegram": {
            "channel_configured": bool(telegram.channel),
            "admin_chat_configured": bool(telegram.admin_chat),
            "bot_token_configured": bool(telegram.token),
            "user_api_configured": bool(telegram.api_id and telegram.api_hash),
        },
    }


def validate_operational_config():
    """Retorna problemas de configuração sem revelar valores sensíveis."""
    problems = []
    telegram = TelegramConfig.from_env()
    publisher = PublisherConfig.from_env()
    monitor = MonitorConfig.from_env()

    if not telegram.token:
        problems.append("TELEGRAM_TOKEN ausente")
    if not telegram.channel:
        problems.append("TELEGRAM_CANAL ausente")
    if not telegram.api_id.isdigit():
        problems.append("TG_API_ID ausente ou inválido")
    if not telegram.api_hash:
        problems.append("TG_API_HASH ausente")
    if not monitor.chats:
        problems.append("TG_CHATS vazio")
    if (
        publisher.recovered_max_age_minutes
        > publisher.offer_max_age_minutes
    ):
        problems.append("idade de recuperadas maior que idade geral")
    return problems


if __name__ == "__main__":
    import json
    from pathlib import Path

    try:
        from dotenv import load_dotenv
        load_dotenv(
            Path(__file__).resolve().with_name(".env"),
            encoding="utf-8-sig",
        )
    except ImportError:
        pass

    print(json.dumps(operational_snapshot(), indent=2, ensure_ascii=False))
    problems = validate_operational_config()
    if problems:
        print("AVISOS:")
        for problem in problems:
            print("-", problem)
    else:
        print("CONFIGURACAO_OPERACIONAL=OK")
