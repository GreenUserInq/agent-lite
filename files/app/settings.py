# Настройки лайта для общих модулей с полным агентом (screen, hands, browser, codetools): тот же интерфейс, что у settings.py агента.
# Данные — в папке агента (над app\): screens\, browser_profile\, agent_tasks\. Свои значения — в app\config.json.
import json, pathlib

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent
_cfg = {}
try:
    _cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8-sig"))
except (OSError, ValueError):
    pass

def get(key, default=None):
    return _cfg.get(key, default)

OLLAMA = get("ollama", "http://127.0.0.1:11434")
