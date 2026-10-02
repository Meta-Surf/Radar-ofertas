import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from telegram_api import TelegramSendError, response_message_id, send_telegram


def response(data, status=200):
    item = Mock()
    item.status_code = status
    item.json.return_value = data
    return item


class TelegramAPITests(unittest.TestCase):
    def test_success_requires_message_id(self):
        self.assertEqual(
            response_message_id(response({"ok": True, "result": {"message_id": 42}})),
            42,
        )
        with self.assertRaises(TelegramSendError) as ctx:
            response_message_id(response({"ok": True, "result": {}}))
        self.assertEqual(ctx.exception.kind, "uncertain")

    def test_400_is_permanent(self):
        with self.assertRaises(TelegramSendError) as ctx:
            response_message_id(response({
                "ok": False, "error_code": 400,
                "description": "Bad Request: can't parse entities",
            }, 400))
        self.assertEqual(ctx.exception.kind, "permanent")
        self.assertTrue(ctx.exception.discard)


    def test_429_uses_retry_after(self):
        with self.assertRaises(TelegramSendError) as ctx:
            response_message_id(response({
                "ok": False, "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 91},
            }, 429))
        self.assertEqual(ctx.exception.kind, "rate_limit")
        self.assertEqual(ctx.exception.retry_after, 91)
        self.assertTrue(ctx.exception.global_pause)

    def test_configuration_and_5xx_are_distinct(self):
        with self.assertRaises(TelegramSendError) as config:
            response_message_id(response({
                "ok": False, "error_code": 403, "description": "Forbidden"
            }, 403))
        self.assertEqual(config.exception.kind, "configuration")
        self.assertEqual(config.exception.retry_after, 300)

        with self.assertRaises(TelegramSendError) as transient:
            response_message_id(response({
                "ok": False, "error_code": 502, "description": "Bad Gateway"
            }, 502))
        self.assertEqual(transient.exception.kind, "transient")
        self.assertEqual(transient.exception.retry_after, 30)


    def test_invalid_json_is_uncertain(self):
        item = Mock()
        item.status_code = 200
        item.json.side_effect = ValueError("invalid")
        with self.assertRaises(TelegramSendError) as ctx:
            response_message_id(item)
        self.assertEqual(ctx.exception.kind, "uncertain")

    def test_network_timeout_is_uncertain_and_not_retried(self):
        requests_module = Mock()
        requests_module.post.side_effect = TimeoutError("timeout")
        with self.assertRaises(TelegramSendError) as ctx:
            send_telegram(
                requests_module, "fake",
                {"chat_id": "@fake", "text": "Oferta"},
            )
        self.assertEqual(ctx.exception.kind, "uncertain")
        requests_module.post.assert_called_once()

    def test_media_400_falls_back_to_text_once(self):
        bad = response({
            "ok": False, "error_code": 400,
            "description": "Bad Request: PHOTO_INVALID_DIMENSIONS",
        }, 400)
        good = response({"ok": True, "result": {"message_id": 77}}, 200)
        requests_module = Mock()
        requests_module.post.side_effect = [bad, good]
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "banner.jpg"
            image.write_bytes(b"jpeg")
            message_id = send_telegram(
                requests_module, "fake",
                {"chat_id": "@fake", "parse_mode": "HTML", "caption": "<b>Oferta</b>"},
                image=image, image_mime="image/jpeg",
            )
        self.assertEqual(message_id, 77)
        self.assertEqual(requests_module.post.call_count, 2)
        first, second = requests_module.post.call_args_list
        self.assertTrue(first.args[0].endswith("/sendPhoto"))
        self.assertTrue(second.args[0].endswith("/sendMessage"))
        self.assertEqual(second.kwargs["data"]["text"], "<b>Oferta</b>")


if __name__ == "__main__":
    unittest.main()
