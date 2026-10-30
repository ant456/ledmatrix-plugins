"""The plugin as the display controller sees it, against a minimal stand-in for the core."""
import logging
import os
import pathlib
import socket
import sys
import time
import types
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
os.environ["LEDMATRIX_GOVEE_CONFIG"] = str(HERE / "no-such-central.json")

try:  # pragma: no cover - depends on the environment
    import src.plugin_system.base_plugin  # noqa: F401
except ImportError:
    class _Base:
        def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
            self.plugin_id, self.config = plugin_id, config
            self.display_manager, self.cache_manager, self.plugin_manager = display_manager, cache_manager, plugin_manager
            self.logger = logging.getLogger(plugin_id)
            self.enabled = config.get("enabled", True)

        def get_info(self):
            return {"id": self.plugin_id}

        def cleanup(self):
            pass

    for name in ("src", "src.plugin_system", "src.plugin_system.base_plugin"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["src.plugin_system.base_plugin"].BasePlugin = _Base

import manager  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make(config):
    return manager.GoveeLightsPlugin("govee-lights", config, types.SimpleNamespace(), types.SimpleNamespace(), types.SimpleNamespace())


def fetch(port, path="/", tries=40):
    for _ in range(tries):
        try:
            return urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=2).read().decode()
        except OSError:
            time.sleep(0.1)
    raise AssertionError("portal did not answer")


def test_serves_the_portal_and_shows_nothing_on_the_panel():
    port = free_port()
    p = make({"enabled": True, "portal_port": port})
    assert "GOVEE LIGHTS" in fetch(port)
    assert '"events"' in fetch(port, "/api/state")
    assert p.display() is False
    assert p.get_info()["portal_running"] is True
    p.update()
    p.cleanup()


def test_disabled_plugin_starts_no_server():
    port = free_port()
    p = make({"enabled": False, "portal_port": port})
    assert p.get_info()["portal_running"] is False


def test_a_busy_port_is_logged_not_fatal():
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen(1)
    p = make({"enabled": True, "portal_port": s.getsockname()[1]})   # already taken
    time.sleep(0.5)
    assert p.display() is False                                       # the plugin itself carries on
    s.close()
