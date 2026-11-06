# Ядро агента: чат с моделью в Ollama + рисование картинок через ComfyUI (модель Anima).
# Других возможностей нет: агент не выполняет команды и не трогает файлы на компьютере.
import json, pathlib, random, time, urllib.error, urllib.parse, urllib.request

VERSION = "1.1.1"  # версия окна агента; make_release.py ставит её сам
APP = pathlib.Path(__file__).parent
ROOT = APP.parent
IMAGES_DIR = ROOT / "images"
CONFIG = {
    "ollama": "http://127.0.0.1:11434",
    "comfy": "http://127.0.0.1:8188",
    "model": "agent-chat",
    "temperature": 0.7,
    "num_ctx": 16384,        # память модели в токенах (16k целиком на видеокарте 12 ГБ)
    "reply_reserve": 3072,   # всегда оставлять на ответ
    "max_steps": 15,         # вызовов инструментов подряд на одно сообщение (потом — отчёт и «продолжай»)
    "same_call_limit": 3,    # тот же вызов в третий раз за сообщение — цикл, не выполняется
    "keep_full_results": 6,  # последние ответы инструментов — целиком, старые сокращаются
    "max_history": 120,      # сообщений в памяти беседы (текущая задача не обрезается)
    "update_url": "https://raw.githubusercontent.com/GreenUserInq/agent-lite/main/update.json",
}
try:  # свои настройки можно положить в app\config.json
    CONFIG.update(json.loads((APP / "config.json").read_text(encoding="utf-8")))
except (OSError, ValueError):
    pass

SIZES = {"portrait": (832, 1216), "landscape": (1216, 832), "square": (1024, 1024)}
PREFIX = "masterpiece, best quality, score_7, "
NEGATIVE = "worst quality, low quality, score_1, score_2, score_3, artist name, blurry, jpeg artifacts, chromatic aberration"

def http(url, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}

# ---------- Рисование ----------

class StopRequested(Exception):
    pass

stop_flag = {"stop": False}

def workflow(prompt, negative, width, height, seed):
    """Anima turbo: 10 шагов euler, cfg 1 (как в рабочей сборке)."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "anima-turbo-v1.1.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": negative}},
        "7": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
                                                   "seed": seed, "steps": 10, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "agent/img"}},
    }

def run_comfy(wf, timeout=900):
    pid = http(f"{CONFIG['comfy']}/prompt", {"prompt": wf})["prompt_id"]
    t = time.time()
    while True:
        h = http(f"{CONFIG['comfy']}/history/{pid}", timeout=30)
        if pid in h:
            break
        if stop_flag["stop"]:
            raise StopRequested()
        if time.time() - t > timeout:
            raise RuntimeError("картинка не готова за 15 минут")
        time.sleep(0.7)
    st = h[pid].get("status", {})
    if st.get("status_str") == "error":
        raise RuntimeError(json.dumps(st.get("messages", [])[-1:], ensure_ascii=False)[:800])
    out = []
    for o in h[pid]["outputs"].values():
        for f in o.get("images", []):
            q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f["subfolder"], "type": f["type"]})
            with urllib.request.urlopen(f"{CONFIG['comfy']}/view?{q}", timeout=120) as r:
                out.append(r.read())
    return out

def unload_llm():
    """Освободить видеопамять от модели чата перед рисованием (обе вместе не помещаются)."""
    try:
        http(f"{CONFIG['ollama']}/api/generate", {"model": CONFIG["model"], "keep_alive": 0}, timeout=60)
    except Exception:
        pass

def free_comfy():
    try:
        http(f"{CONFIG['comfy']}/free", {"unload_models": True, "free_memory": True}, timeout=30)
        time.sleep(2)
    except Exception:
        pass

def generate_image(prompt, negative="", orientation="portrait", count=1, progress=print):
    count = max(1, min(int(count or 1), 4))
    w, h = SIZES.get(orientation or "portrait", SIZES["portrait"])
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    unload_llm()
    saved = []
    try:
        for i in range(count):
            if stop_flag["stop"]:
                break
            progress(f"рисую картинку {i + 1}/{count}…")
            t = time.time()
            for data in run_comfy(workflow(PREFIX + prompt, NEGATIVE + (", " + negative if negative else ""), w, h,
                                           random.randint(0, 2**48))):
                dst = IMAGES_DIR / f"img_{time.strftime('%Y%m%d_%H%M%S')}_{i + 1}.png"
                dst.write_bytes(data)
                saved.append(str(dst))
                progress(f"готово за {time.time() - t:.0f} с")
    except StopRequested:
        return "Остановлено пользователем." + ("\nУспело:\n" + "\n".join(saved) if saved else "")
    except urllib.error.URLError as e:
        return f"Программа рисования (ComfyUI) не отвечает: {e}. Перезапусти агента ярлыком."
    except RuntimeError as e:
        return f"Ошибка рисования: {e}" + ("\nУспело:\n" + "\n".join(saved) if saved else "")
    finally:
        free_comfy()
    return "Сохранено:\n" + "\n".join(saved)

# ---------- Модель чата ----------

TOOLS = [{"type": "function", "function": {
    "name": "generate_image",
    "description": "Нарисовать картинку (аниме, иллюстрации, арт, персонажи, пейзажи — модель Anima). Вызывай, только когда просят нарисовать. "
                   "prompt — ТОЛЬКО на английском: сначала теги через запятую с маленькой буквы (кто/что, внешность, одежда, поза, место, свет, стиль), "
                   "потом 1-2 предложения описания. Около 10 секунд на картинку.",
    "parameters": {"type": "object", "properties": {
        "prompt": {"type": "string"},
        "negative": {"type": "string", "description": "что убрать с картинки, по-английски (необязательно)"},
        "orientation": {"type": "string", "enum": list(SIZES), "description": "portrait — вертикальная (по умолчанию), landscape — горизонтальная, square"},
        "count": {"type": "integer", "description": "сколько вариантов, 1-4"}},
        "required": ["prompt"]}}}]

SYSTEM = """Ты дружелюбный помощник на компьютере пользователя. Отвечай по-русски, понятно и по делу.
О себе: ты локальная языковая модель в Ollama на этом компьютере (без облака); картинки рисует ComfyUI здесь же.
Ты умеешь разговаривать и рисовать картинки инструментом generate_image. Других возможностей у тебя нет:
ты не выходишь в интернет, не видишь файлы и не управляешь компьютером — если просят такое, честно скажи об этом.
Когда просят нарисовать — сразу вызывай generate_image (не обещай «сейчас нарисую»), промпт пиши по-английски.
Картинка появится в окне сама, ссылку на файл повторять не нужно. Не выдумывай свежие новости и текущие данные."""

_ctx = {"chars_per_token": 3.2}  # замер на русских беседах ~3.3; уточняется по каждому ответу Ollama

def fit_context(messages, tools):
    """Обрезать историю ПО ТОКЕНАМ, чтобы на ответ всегда оставалось reply_reserve токенов (иначе ответ обрывается
    на полуслове). Выкидываются самые старые реплики целиком; текущая — никогда. Возвращает число символов."""
    fixed = len(json.dumps(tools, ensure_ascii=False)) if tools else 0
    size = lambda: fixed + len(json.dumps(messages, ensure_ascii=False))
    budget = (CONFIG["num_ctx"] - CONFIG["reply_reserve"]) * _ctx["chars_per_token"]
    while size() > budget:
        users = [i for i, m in enumerate(messages) if i > 0 and m.get("role") == "user"]
        if len(users) < 2:
            break
        del messages[1:users[1]]
    return size()

def chat(messages, think=False, use_tools=True):
    tools = TOOLS if use_tools else None
    sent = fit_context(messages, tools)
    body = {"model": CONFIG["model"], "messages": messages, "tools": tools, "stream": False, "think": bool(think),
            "options": {"temperature": CONFIG["temperature"], "num_ctx": CONFIG["num_ctx"]}}
    if not use_tools:
        body.pop("tools")
    for attempt in range(3):
        try:
            r = http(f"{CONFIG['ollama']}/api/chat", body, timeout=600)
            if r.get("prompt_eval_count"):
                _ctx["chars_per_token"] = max(1.5, min(5.0, sent / r["prompt_eval_count"]))
            msg = r["message"]
            if r.get("done_reason") == "length":
                msg["content"] = (msg.get("content") or "") + "\n\n…(ответ оборвался. Напиши «продолжай» — допишу.)"
            return msg
        except urllib.error.HTTPError:
            if attempt == 2:
                raise
            time.sleep(5)  # модель не загрузилась (память ещё занята рисованием) — подождать

FUNCS = {"generate_image": generate_image}

# ---------- Длинные задачи: защита от цикла, сокращение старого, отчёт при лимите ----------

class LoopGuard:
    """Тот же инструмент с теми же аргументами в третий раз за сообщение — цикл: не выполняем, говорим модели."""
    def __init__(self):
        self.seen = {}

    def check(self, name, args):
        key = name + json.dumps(args, ensure_ascii=False, sort_keys=True)
        self.seen[key] = self.seen.get(key, 0) + 1
        if self.seen[key] >= CONFIG["same_call_limit"]:
            return (f"НЕ ВЫПОЛНЕНО: ты уже {self.seen[key] - 1} раза вызывал {name} с теми же аргументами. "
                    "Повтор ничего не даст — сделай по-другому или напиши пользователю, что не получается.")
        return None

def compact_results(messages):
    """Старые ответы инструментов (кроме последних) сократить — иначе длинная задача переполнит память модели."""
    tools = [m for m in messages if m.get("role") == "tool"]
    for m in tools[:-CONFIG["keep_full_results"]]:
        c = m.get("content") or ""
        if len(c) > 700 and not c.startswith("(сокращено)"):
            m["content"] = "(сокращено) " + c[:400] + " … " + c[-200:]

def wrap_up(messages, think=False):
    """Шаги кончились: модель БЕЗ инструментов пишет, что сделано и что осталось."""
    note = {"role": "user", "content": "[Служебное: лимит шагов на одно сообщение исчерпан. НЕ вызывай инструменты. "
                                       "Коротко напиши пользователю: что уже сделано, что осталось сделать. "
                                       "Скажи, что можно написать «продолжай» — и ты продолжишь с этого места.]"}
    try:
        text = (chat(messages + [note], think, use_tools=False).get("content") or "").strip()
    except Exception:
        text = ""
    text = text or "Задача не закончена за один раз. Напиши «продолжай» — продолжу с этого места."
    messages.append({"role": "assistant", "content": text})
    return text

def trim_history(messages):
    """system + последние сообщения, но текущую задачу (с последней реплики пользователя) не резать."""
    if len(messages) <= CONFIG["max_history"]:
        return messages
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=1)
    start = min(len(messages) - CONFIG["max_history"], last_user)
    while start < last_user and messages[start].get("role") != "user":
        start += 1
    return messages[:1] + messages[max(1, start):]
