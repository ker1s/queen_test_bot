"""Запросы к RouterAI: характеристика и отключённая в боте генерация портрета."""

import json
import logging
import time

import requests


logger = logging.getLogger(__name__)
CHAT_COMPLETIONS_URL = "https://routerai.ru/api/v1/chat/completions"


class RouterAIClient:
    def __init__(self, api_key, model, report_prompt, image_model=None, image_prompt=None):
        self.api_key = api_key
        self.model = model
        self.report_prompt = report_prompt
        self.image_model = image_model
        self.image_prompt = image_prompt

    def _chat_completion(self, model, messages, timeout):
        response = requests.post(
            CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "messages": messages},
            timeout=timeout,
        )
        response.raise_for_status()
        # Decode the original bytes, regardless of an incorrect HTTP charset.
        payload = json.loads(response.content.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise RuntimeError("RouterAI returned an invalid response")
        if payload.get("error"):
            raise RuntimeError(f"RouterAI request failed: {payload['error']}")
        try:
            choice = payload["choices"][0]
            if choice.get("error"):
                raise RuntimeError(f"RouterAI request failed: {choice['error']}")
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError("Invalid message")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise RuntimeError("RouterAI returned no message") from exc
        if message.get("refusal"):
            raise RuntimeError(f"RouterAI refused the request: {message['refusal']}")
        usage = payload.get("usage") or {}
        logger.info("RouterAI response: model=%s, cost=%s RUB", payload.get("model", model), usage.get("cost"))
        return message

    def generate_characteristic(self, answers_json):
        message = self._chat_completion(
            self.model,
            [
                {"role": "system", "content": self.report_prompt},
                {"role": "user", "content": answers_json},
            ],
            timeout=180,
        )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("RouterAI returned no characteristic text")
        return content.strip()

    def generate_image(self, description, retries=2, backoff=3):
        """Сохранено для будущего включения портретов; бот этот метод не вызывает."""
        if not self.image_model or not self.image_prompt:
            raise ValueError("Set the image model and image prompt to generate portraits")
        for attempt in range(retries + 1):
            message = self._chat_completion(
                self.image_model,
                [{
                    "role": "user",
                    "content": f"{self.image_prompt}\n\nХарактеристика:\n{description}",
                }],
                timeout=240,
            )
            for item in message.get("images") or []:
                resource = item.get("image_url")
                url = resource.get("url") if isinstance(resource, dict) else resource
                if isinstance(url, str) and url:
                    return {"kind": "url", "data": url}
            content = message.get("content")
            if isinstance(content, str) and content.startswith("data:image/"):
                return {"kind": "url", "data": content}
            logger.warning("No image found in RouterAI response on attempt %d", attempt + 1)
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
        raise RuntimeError("No image payload found in RouterAI response")
