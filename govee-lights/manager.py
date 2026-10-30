"""Govee Lights: the web portal for the score-triggered light effects in the sports plugins.

This plugin shows nothing on the panel. It hosts a small web page (port 5020 by default) where you pick
the lights, pick an effect for each moment, and press Test. The page saves one file,
<LEDMatrix>/config/govee_fx.json, which the NFL, NBA and NHL plugins re-read whenever it changes.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict

from src.plugin_system.base_plugin import BasePlugin

import govee_fx
from portal import Portal, create_app

HERE = Path(__file__).resolve().parent


class GoveeLightsPlugin(BasePlugin):
    def __init__(self, plugin_id: str, config: Dict[str, Any], display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.port = int(config.get("portal_port", 5020))
        self.portal = Portal(logger=self.logger)
        self._server_started = False
        if self.enabled:
            self._start_portal()

    def _start_portal(self) -> None:
        try:
            app = create_app(self.portal, HERE / "static")
        except ImportError:
            self.logger.warning("Flask is not installed, so the Govee Lights portal is off (pip install Flask)")
            return

        def run() -> None:
            try:
                app.run(host="0.0.0.0", port=self.port, debug=False, use_reloader=False, threaded=True)
            except (OSError, SystemExit) as exc:          # Flask exits the thread when the port is taken
                self._server_started = False
                self.logger.error("Govee Lights portal could not start on port %d (%s). Is something else on it? "
                                  "Set portal_port.", self.port, exc)

        threading.Thread(target=run, daemon=True, name="govee-lights-portal").start()
        self._server_started = True
        self.logger.info("Govee Lights portal on port %d (settings file: %s)", self.port, self.portal.central)

    # Nothing to draw: the display controller just moves on.
    def update(self) -> None:
        pass

    def display(self, force_clear: bool = False, display_mode: str = "") -> bool:
        return False

    def get_display_duration(self) -> float:
        return 1.0

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update({"portal_port": self.port, "portal_running": self._server_started,
                     "settings_file": str(self.portal.central), "settings_saved": self.portal.exists()})
        return info

    def cleanup(self) -> None:
        super().cleanup()
