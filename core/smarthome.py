"""
My Jarvis smart home integration
- Home Assistant REST API
- Philips Hue (Fallback)
"""
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)


class SmartHomeManager:
    def __init__(self, config: dict):
        self.config = config

    @property
    def is_configured(self) -> bool:
        return bool(self.config.get("ha_url") and self.config.get("ha_token"))

    @property
    def ha_url(self) -> str:
        url = self.config.get("ha_url", "").rstrip("/")
        return url

    @property
    def ha_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.config.get('ha_token', '')}",
            "Content-Type": "application/json",
        }

    def get_entities(self, domain: str = "") -> list:
        if not self.is_configured:
            return []
        try:
            r = requests.get(
                f"{self.ha_url}/api/states",
                headers=self.ha_headers, timeout=10
            )
            r.raise_for_status()
            entities = r.json()
            if domain:
                entities = [e for e in entities if e["entity_id"].startswith(domain + ".")]
            return [{
                "entity_id": e["entity_id"],
                "state": e["state"],
                "name": e.get("attributes", {}).get("friendly_name", e["entity_id"]),
            } for e in entities]
        except Exception as e:
            logger.error("[SmartHome] fetching the entities failed: %s", e)
            return []

    def call_service(self, domain: str, service: str,
                     entity_id: str, **kwargs) -> bool:
        if not self.is_configured:
            return False
        try:
            data = {"entity_id": entity_id}
            data.update(kwargs)
            r = requests.post(
                f"{self.ha_url}/api/services/{domain}/{service}",
                headers=self.ha_headers,
                json=data, timeout=10
            )
            r.raise_for_status()
            logger.info("[SmartHome] %s.%s -> %s", domain, service, entity_id)
            return True
        except Exception as e:
            logger.error("[SmartHome] the service call failed: %s", e)
            return False

    def light_on(self, entity_id: str, brightness: int = 255) -> bool:
        return self.call_service("light", "turn_on", entity_id, brightness=brightness)

    def light_off(self, entity_id: str) -> bool:
        return self.call_service("light", "turn_off", entity_id)

    def set_temperature(self, entity_id: str, temperature: float) -> bool:
        return self.call_service("climate", "set_temperature", entity_id,
                                temperature=temperature)

    def play_media(self, entity_id: str) -> bool:
        return self.call_service("media_player", "media_play", entity_id)

    def stop_media(self, entity_id: str) -> bool:
        return self.call_service("media_player", "media_stop", entity_id)

    def get_status_text(self) -> str:
        if not self.is_configured:
            return ("Smart home is not configured. Please add the Home Assistant URL and token "
                    "in the settings.")
        lights = self.get_entities("light")
        climate = self.get_entities("climate")
        media = self.get_entities("media_player")

        lines = []
        if lights:
            on_lights = [l for l in lights if l["state"] == "on"]
            lines.append(f"**Lichter:** {len(on_lights)}/{len(lights)} an")
        if climate:
            for c in climate:
                lines.append(f"**{c['name']}:** {c['state']}")
        if media:
            playing = [m for m in media if m["state"] == "playing"]
            if playing:
                lines.append(f"**Media:** {len(playing)} playing")

        return "\n".join(lines) if lines else "No smart home devices found."


class HueBridge:
    """A direct Philips Hue fallback (without Home Assistant)."""

    def __init__(self, config: dict):
        self.config = config
        self._bridge = None
        self._init()

    def _init(self):
        bridge_ip = self.config.get("hue_bridge_ip", "")
        if not bridge_ip:
            return
        try:
            from phue import Bridge
            self._bridge = Bridge(bridge_ip)
            self._bridge.connect()
            logger.info("[Hue] Bridge verbunden: %s", bridge_ip)
        except ImportError:
            logger.debug("[Hue] phue is not installed")
        except Exception as e:
            logger.error("[Hue] The connection failed: %s", e)

    @property
    def is_configured(self) -> bool:
        return self._bridge is not None

    def light_on(self, light_name: str, brightness: int = 254):
        if not self._bridge:
            return False
        try:
            self._bridge.set_light(light_name, "on", True)
            self._bridge.set_light(light_name, "bri", brightness)
            return True
        except Exception as e:
            logger.error("[Hue] turning the light on failed: %s", e)
            return False

    def light_off(self, light_name: str):
        if not self._bridge:
            return False
        try:
            self._bridge.set_light(light_name, "on", False)
            return True
        except Exception as e:
            logger.error("[Hue] turning the light off failed: %s", e)
            return False
