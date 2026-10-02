"""Tratamento uniforme das respostas da Bot API do Telegram."""
import json
import re
from pathlib import Path


class TelegramSendError(Exception):
    def __init__(self, kind, description, *, error_code=0, retry_after=0):
        self.kind = str(kind)
        self.error_code = int(error_code or 0)
        self.retry_after = max(0, int(retry_after or 0))
        clean = re.sub(r"[\r\n\t]+", " ", str(description or "")).strip()
        self.description = clean[:240] or "erro não detalhado"
        super().__init__(self.description)

    @property
    def discard(self):
        return self.kind == "permanent"

    @property
    def global_pause(self):
        return self.kind in {"rate_limit", "configuration"}


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def response_message_id(response):
    status = _integer(getattr(response, "status_code", 0))
    try:
        data = response.json()
    except Exception:
        raise TelegramSendError(
            "uncertain",
            "Telegram retornou resposta sem JSON válido.",
            error_code=status,
        ) from None

    if not isinstance(data, dict):
        raise TelegramSendError(
            "uncertain", "Telegram retornou formato inesperado.", error_code=status
        )
    if data.get("ok") is True:
        message_id = _integer((data.get("result") or {}).get("message_id"))
        if message_id <= 0:
            raise TelegramSendError(
                "uncertain",
                "Telegram confirmou a chamada sem message_id válido.",
                error_code=status,
            )
        return message_id

    code = _integer(data.get("error_code"), status)
    description = data.get("description") or f"Telegram HTTP {status or code or 'desconhecido'}"
    if code == 429:
        retry = _integer((data.get("parameters") or {}).get("retry_after"), 60)
        raise TelegramSendError(
            "rate_limit", description, error_code=code, retry_after=max(1, min(retry, 3600))
        )

    if code == 400:
        raise TelegramSendError("permanent", description, error_code=code)
    if code in {401, 403, 404}:
        raise TelegramSendError(
            "configuration", description, error_code=code, retry_after=300
        )
    if code >= 500 or status >= 500:
        raise TelegramSendError(
            "transient", description, error_code=code or status, retry_after=30
        )
    if 400 <= code < 500:
        raise TelegramSendError("permanent", description, error_code=code)
    raise TelegramSendError(
        "uncertain", description, error_code=code or status
    )


def is_media_error(error):
    if not isinstance(error, TelegramSendError) or error.error_code != 400:
        return False
    text = error.description.casefold()
    return any(word in text for word in (
        "photo", "image", "media", "file identifier", "file_id",
        "wrong type of the web page content", "failed to get http url content",
    ))


def _post(requests_module, token, data, image=None, image_mime=None, timeout=(10, 45)):
    payload = dict(data)
    method = "sendPhoto" if image else "sendMessage"
    endpoint = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if isinstance(image, str):
            payload["photo"] = image
            response = requests_module.post(endpoint, data=payload, timeout=timeout)
        elif image:
            path = Path(image)
            with path.open("rb") as photo:
                file_value = (path.name, photo, image_mime) if image_mime else (path.name, photo)
                response = requests_module.post(
                    endpoint, data=payload, files={"photo": file_value}, timeout=timeout
                )
        else:
            payload["link_preview_options"] = json.dumps({"is_disabled": True})
            response = requests_module.post(endpoint, data=payload, timeout=timeout)
    except Exception as error:
        # Timeout/conexão interrompida pode ocorrer depois de o Telegram receber
        # a requisição; nunca repetir automaticamente esse envio.
        raise TelegramSendError(
            "uncertain", f"Falha de rede ao enviar ao Telegram: {type(error).__name__}"
        ) from None
    return response_message_id(response)


def send_telegram(requests_module, token, data, image=None, image_mime=None, timeout=(10, 45)):
    """Envia e só faz fallback sem imagem quando o Telegram rejeita a mídia."""
    if not image:
        return _post(requests_module, token, data, timeout=timeout)

    try:
        return _post(
            requests_module, token, data, image=image,
            image_mime=image_mime, timeout=timeout,
        )
    except TelegramSendError as error:
        if not is_media_error(error):
            raise

    fallback = dict(data)
    if "caption" in fallback:
        fallback["text"] = fallback.pop("caption")
    fallback.pop("photo", None)
    return _post(requests_module, token, fallback, timeout=timeout)
