# Ядро агента: чат с моделью в Ollama + рисование картинок через ComfyUI (модель Anima).
# Работа с кодом — только в папках проектов, которые человек сам подключил в окне («Проекты»): чтение, поиск,
# правка с подтверждением, проверка сборки, снимки git для отката. Команд и других файлов на компьютере агент не трогает.
import json, pathlib, random, re, time, urllib.error, urllib.parse, urllib.request
import codetools

VERSION = "1.3.0"  # версия окна агента; make_release.py ставит её сам
APP = pathlib.Path(__file__).parent
ROOT = APP.parent
IMAGES_DIR = ROOT / "images"
CONFIG = {
    "ollama": "http://127.0.0.1:11434",
    "comfy": "http://127.0.0.1:8188",
    "model": "gemma4:12b",   # модель по умолчанию для новых бесед (первая установленная из models)
    # модели на выбор (список в шапке окна); недостающие окно докачивает само через Ollama
    "models": [
        {"id": "gemma4:12b", "name": "Gemma 4 12B — работа", "pull": True},
        {"id": "huihui_ai/gemma-4-abliterated:12b", "name": "Gemma 4 12B — без цензуры", "pull": True,
         "options": {"repeat_penalty": 1.1, "top_k": 64}},
        {"id": "agent-chat", "name": "Qwen3.5 Defiant (старая)", "pull": False},  # у ставивших раньше 1.2.0
    ],
    "temperature": 0.7,
    "num_ctx": 16384,        # память модели в токенах (16k целиком на видеокарте 12 ГБ)
    "reply_reserve": 3072,   # всегда оставлять на ответ
    "max_steps": 15,         # вызовов инструментов подряд на одно сообщение (потом — отчёт и «продолжай»)
    "max_steps_code": 30,    # то же, когда подключены проекты (поиск, чтение, правка, сборка — шагов больше)
    "projects": {},          # папки проектов с кодом: имя → путь (подключаются в окне)
    "same_call_limit": 3,    # тот же вызов в третий раз за сообщение — цикл, не выполняется
    "keep_full_results": 6,  # последние ответы инструментов — целиком, старые сокращаются
    "max_history": 120,      # сообщений в памяти беседы (текущая задача не обрезается)
    "update_url": "https://raw.githubusercontent.com/GreenUserInq/agent-lite/main/update.json",
}
try:  # свои настройки можно положить в app\config.json
    CONFIG.update(json.loads((APP / "config.json").read_text(encoding="utf-8")))
except (OSError, ValueError):
    pass
codetools.setup(CONFIG.get("projects"), ROOT)
codetools.READ_CHARS, codetools.READ_LINES = 5000, 120  # память модели 16k — читать поменьше за раз

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
    """Освободить видеопамять перед рисованием: выгрузить ВСЕ загруженные модели (в беседах могут быть разные)."""
    try:
        loaded = [m["name"] for m in http(f"{CONFIG['ollama']}/api/ps", timeout=10).get("models", [])]
    except Exception:
        loaded = [current["model"]]
    for name in loaded:
        try:
            http(f"{CONFIG['ollama']}/api/generate", {"model": name, "keep_alive": 0}, timeout=60)
        except Exception:
            pass

# ---------- Модели: какие установлены, выбор, докачка ----------

current = {"model": CONFIG["model"]}          # модель текущей беседы (окно ставит перед ответом)
pulling = {"model": "", "percent": 0.0, "error": ""}  # что сейчас докачивается

def installed_models():
    try:
        return {m["name"] for m in http(f"{CONFIG['ollama']}/api/tags", timeout=10).get("models", [])}
    except Exception:
        return set()

def _has(names, mid):
    return mid in names or (":" not in mid and f"{mid}:latest" in names)

def available_models():
    """Модели из списка, которые уже стоят в Ollama — их и показывает выпадающий список."""
    names = installed_models()
    return [m for m in CONFIG["models"] if _has(names, m["id"])]

def default_model():
    av = [m["id"] for m in available_models()]
    return CONFIG["model"] if CONFIG["model"] in av else (av[0] if av else CONFIG["model"])

def model_options(mid):
    return next((m.get("options") or {} for m in CONFIG["models"] if m["id"] == mid), {})

def pull_missing():
    """Докачать недостающие модели (по очереди, через Ollama — она сама проверяет целостность). Для фонового потока."""
    for m in CONFIG["models"]:
        if not m.get("pull") or _has(installed_models(), m["id"]):
            continue
        pulling.update(model=m.get("name") or m["id"], percent=0.0, error="")
        for attempt in range(6):  # загрузка иногда зависает — повтор продолжает с места остановки
            try:
                req = urllib.request.Request(f"{CONFIG['ollama']}/api/pull", json.dumps({"model": m["id"], "stream": True}).encode(),
                                             {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=300) as r:  # 5 мин без данных — считать зависшей
                    for line in r:
                        d = json.loads(line or b"{}")
                        if d.get("total"):
                            pulling["percent"] = round(100 * d.get("completed", 0) / d["total"], 1)
                        if d.get("error"):
                            raise RuntimeError(d["error"])
                if _has(installed_models(), m["id"]):
                    break
            except Exception as e:
                pulling["error"] = str(e)[:200]
                time.sleep(5)
    pulling.update(model="", percent=0.0)

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

# ---------- Проекты с кодом (только подключённые папки) ----------

CURRENT_REQUEST = "правки"  # последнее сообщение человека — название задачи кода при первой правке

def confirm(question):
    """Спросить разрешения. Окно подменяет на кнопки «Выполнить / Отклонить»; без окна — отказ."""
    return False

def save_projects():
    """Записать список проектов в app\\config.json (остальные свои настройки человека не трогаем)."""
    path = APP / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["projects"] = CONFIG["projects"]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    codetools.setup(CONFIG["projects"], ROOT)

def add_project(folder, name=""):
    """Подключить папку проекта. Вернуть (ok, сообщение)."""
    p = pathlib.Path(str(folder).strip().strip('"'))
    if not p.is_absolute() or not p.is_dir():
        return False, f"Папка не найдена: {folder}"
    p = p.resolve()
    if p == pathlib.Path(p.anchor) or p in (pathlib.Path.home(), ROOT.resolve()) or ROOT.resolve() in p.parents:
        return False, "Это не папка проекта (корень диска, домашняя папка или папка агента) — выбери папку именно с кодом."
    name = re.sub(r"[^\w-]+", "-", (name or p.name).lower()).strip("-") or "project"
    while name in CONFIG["projects"] and pathlib.Path(CONFIG["projects"][name]).resolve() != p:
        name += "-2"
    CONFIG["projects"] = {**CONFIG["projects"], name: str(p)}
    save_projects()
    why = codetools.git_ready(p)
    return True, f"Подключено: {name} — {p}." + (f" Читать и искать можно, а править нельзя: {why}." if why else "")

def remove_project(name):
    if name not in CONFIG["projects"]:
        return False, "Нет такого проекта"
    CONFIG["projects"] = {k: v for k, v in CONFIG["projects"].items() if k != name}
    save_projects()
    return True, f"Отключено: {name}"

def _in_project(path):
    """Путь внутри подключённого проекта — пусто, иначе причина отказа (за пределы проектов агент не ходит)."""
    try:
        name, _ = codetools.project_of(path)
    except Exception:
        name = None
    if not name:
        return ("Отказано: доступны только папки подключённых проектов: " + (codetools.describe_projects()) +
                ". Другую папку человек может подключить сам кнопкой «+ Папка проекта».")
    return ""

def read_file(path, start=0, end=0, **_):
    bad = _in_project(path)
    if bad:
        return bad
    p = pathlib.Path(path)
    if not p.is_file():
        return f"Файл не найден: {path}"
    if not start and not end and p.stat().st_size > codetools.READ_CHARS:
        return ("Файл длинный — оглавление outline, нужное место search_code, потом read_file со start/end.\n" +
                codetools.read_lines(path, 1, 60))
    return codetools.read_lines(path, start or 1, end)

NUMBERED = re.compile(r"(?m)^ *\d+\| ?")  # модель скопировала строки вместе с номерами из read_file

def _before_write(path):
    bad = _in_project(path) or codetools.forbidden(path)
    if bad:
        return bad, ""
    _, root = codetools.project_of(path)
    why = codetools.git_ready(root)
    if why:
        return f"Править нельзя: {why}.", ""
    note = codetools.ensure_task(path, " ".join(CURRENT_REQUEST.split())[:40] or "правки")
    if note.startswith("Не удалось"):
        return note, ""
    return "", note

def _after_write(path):
    return " Проверь сборку: build_check (после всех правок)." if pathlib.Path(path).suffix.lower() == ".cs" else ""

def edit_file(path, old, new, count=1, **_):
    bad = _in_project(path) or codetools.forbidden(path)
    if bad:
        return bad
    p = pathlib.Path(path)
    if not p.is_file():
        return f"Файл не найден: {path}"
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
    except UnicodeDecodeError:
        return f"Файл {path} не в UTF-8 — править его так нельзя."
    if not old:
        return "Ошибка: old пустой — укажи точный кусок из файла (прочитай read_file)."
    if old == new:
        return "Ничего не изменено: new совпадает с old. Напиши в new исправленный вариант."
    n = text.count(old)
    if n == 0 and NUMBERED.search(old):
        old, new = NUMBERED.sub("", old), NUMBERED.sub("", new)
        n = text.count(old)
    if n == 0 and "\r\n" in text and "\r\n" not in old:
        n = text.count(old.replace("\n", "\r\n"))
        if n:
            old = old.replace("\n", "\r\n")
    if n == 0:  # пробелы в конце строк / в пустых строках — модель часто их путает
        loose, _ = codetools.find_loose(text, old)
        if loose is not None:
            old, n = loose, 1
            new = new.strip("\n").replace("\r\n", "\n")
    count = int(count or 1)
    if n == 0:
        return "Такого куска в файле нет. Прочитай нужные строки (read_file) и скопируй old ТОЧНО, без номеров строк."
    if n != count:
        return f"Кусок встречается {n} раз, а ожидалось {count}. Возьми кусок длиннее или укажи count={n}."
    if not confirm(f"Изменить файл {path}:\n--- было:\n{old[:600]}\n+++ станет:\n{new[:600]}"):
        return "Пользователь отклонил правку."
    bad, note = _before_write(path)
    if bad:
        return "Отказано: " + bad
    if "\r\n" in text and "\r\n" not in new:
        new = new.replace("\n", "\r\n")
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    p.write_bytes(bom + text.replace(old, new).encode("utf-8"))
    return (note + " " if note else "") + f"Исправлено: {path} (замен: {n}), откат — code_task_undo." + _after_write(path)

def write_file(path, content, **_):
    bad = _in_project(path) or codetools.forbidden(path)
    if bad:
        return bad
    p = pathlib.Path(path)
    lines = content.count("\n") + 1
    if p.exists():
        old = p.read_bytes()
        if len(old) > 2048 and len(content.encode("utf-8")) < 0.7 * len(old):
            return (f"Отказано: новый текст ({lines} строк) намного короче файла — похоже, обрезан. "
                    "Существующие файлы правь через edit_file небольшими кусками.")
        old_lines = old.count(b"\n") + 1
        q = f"Перезаписать файл {path}: было {old_lines} строк → станет {lines}"
    else:
        q = f"Создать файл {path}: {lines} строк"
    if not confirm(q):
        return "Пользователь отклонил запись."
    bad, note = _before_write(path)
    if bad:
        return "Отказано: " + bad
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return (note + " " if note else "") + f"Записано: {path} (откат — code_task_undo)." + _after_write(path)

def list_dir(path, **_):
    bad = _in_project(path)
    if bad:
        return bad
    p = pathlib.Path(path)
    if not p.is_dir():
        return f"Папка не найдена: {path}"
    rows = [f"[папка] {e.name}" if e.is_dir() else f"{e.name}  ({e.stat().st_size // 1024} КБ)"
            for e in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))[:200]
            if e.name not in codetools.SKIP_DIRS]
    return "\n".join(rows) or "(пусто)"

def search_code(pattern, project="", path="", ext="", regex=False, **_):
    if path:
        bad = _in_project(path)
        if bad:
            return bad
    return codetools.search_code(pattern, project, path, ext, regex)

def outline(path, **_):
    return _in_project(path) or codetools.outline(path)

S = {"type": "string"}

def _tool(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}

def code_tools():
    names = ", ".join(codetools.PROJECTS)
    return [
        _tool("search_code", "Найти текст в файлах проекта (как grep): «путь:строка: текст». Ищи имена классов, методов, полей.",
              {"pattern": {"type": "string", "description": "что искать (без учёта регистра)"},
               "project": {"type": "string", "description": f"проект: {names} (можно не указывать, если один)"},
               "path": {"type": "string", "description": "искать только в этой папке или файле (необязательно)"},
               "ext": {"type": "string", "description": "расширения через запятую: cs, json, md (необязательно)"},
               "regex": {"type": "boolean", "description": "pattern — регулярное выражение"}}, ["pattern"]),
        _tool("outline", "Оглавление файла кода: классы, методы, свойства с номерами строк. Смотри перед чтением большого файла.",
              {"path": S}, ["path"]),
        _tool("read_file", "Прочитать строки файла проекта с номерами (start/end, до 120 строк за раз).",
              {"path": S, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"]),
        _tool("list_dir", "Показать содержимое папки проекта.", {"path": S}, ["path"]),
        _tool("edit_file", "Исправить файл проекта: заменить кусок old на new (человек подтверждает). "
              "old — точная копия строк из read_file БЕЗ номеров строк.",
              {"path": S, "old": {"type": "string", "description": "точный кусок текста из файла"},
               "new": {"type": "string", "description": "чем заменить"},
               "count": {"type": "integer", "description": "сколько раз встречается old (по умолчанию 1)"}}, ["path", "old", "new"]),
        _tool("write_file", "Создать НОВЫЙ файл в проекте (человек подтверждает). Существующий — только edit_file.",
              {"path": S, "content": S}, ["path", "content"]),
        _tool("build_check", "Проверить, что C#-код проекта Unity компилируется (без Unity, ~20 с). Вызывай после правок .cs.",
              {"project": S}, []),
        _tool("code_task_status", "Какие файлы изменены в текущей (или последней) задаче кода.", {"project": S}, []),
        _tool("code_task_diff", "Изменения задачи кода (diff): все или одного файла.",
              {"project": S, "file": {"type": "string", "description": "путь от корня проекта (необязательно)"}}, []),
        _tool("code_task_finish", "Закончить задачу кода (снимок «после»). Вызывай, когда всё сделано и сборка без ошибок.",
              {"project": S, "summary": {"type": "string", "description": "что сделано, 1-3 строки"}}, []),
        _tool("code_task_undo", "Откатить задачу кода: вернуть файлы к снимку «до». Только по просьбе человека.", {"project": S}, []),
    ]

CODE_FUNCS = {
    "search_code": search_code, "outline": outline, "read_file": read_file, "list_dir": list_dir,
    "edit_file": edit_file, "write_file": write_file,
    "build_check": lambda project="", **_: codetools.build_check(project),
    "code_task_status": lambda project="", **_: codetools.task_status(project),
    "code_task_diff": lambda project="", file="", **_: codetools.task_diff(project, file),
    "code_task_finish": lambda project="", summary="", **_: codetools.task_finish(project, summary),
    "code_task_undo": lambda project="", **_: codetools.task_undo(project, confirm=lambda q: confirm(q)),
}

CODE_SYSTEM = """
Ещё ты помогаешь с кодом в подключённых папках проектов: {projects}. Только эти папки — другие файлы ты не видишь.
1. Найди место: search_code (имя класса, метода, текст), оглавление файла — outline. Большие файлы не читай целиком —
   read_file со start/end вокруг нужных строк.
2. Прежде чем звать метод или поле — найди его объявление (search_code), не придумывай имена.
3. Правь edit_file маленькими кусками; old — точная копия строк из read_file БЕЗ номеров и «|». Человек подтверждает каждую правку.
4. После правок .cs — build_check; ошибки исправляй по одной. Не вышло за 3 попытки — остановись и расскажи человеку.
5. Первая правка сама делает снимок «до» (ветка git agent/...). Закончил и сборка без ошибок — code_task_finish и короткий отчёт:
   какие файлы изменены и что проверить. Откат — code_task_undo, только если человек просит.
Файлы .meta, .unity, .prefab не трогай. Комментарии в коде — на языке соседнего кода, стиль — как в соседнем коде."""

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
Говори «готово» только если инструмент это подтвердил; ответил ошибкой — так и скажи пользователю.
Картинка появится в окне сама, ссылку на файл повторять не нужно. Не выдумывай свежие новости и текущие данные."""

def tools():
    """Инструменты сейчас: рисование + код, если подключены проекты."""
    return TOOLS + (code_tools() if codetools.PROJECTS else [])

def system_prompt():
    if not codetools.PROJECTS:
        return SYSTEM
    return (SYSTEM.replace("не видишь файлы и не управляешь компьютером", "не управляешь компьютером")
            + CODE_SYSTEM.format(projects=codetools.describe_projects()))

def max_steps():
    return CONFIG["max_steps_code"] if codetools.PROJECTS else CONFIG["max_steps"]

# Служебные метки шаблона модели, которые просачиваются в аргументы (Gemma 4: «<|channel>:» перед строками кода)
MODEL_TOKENS = re.compile(r"<\|?/?(?:channel|message|start|end|im_start|im_end|turn|tool_call|eot)[^>\n]{0,20}\|?>:? ?")

def clean_args(args):
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return MODEL_TOKENS.sub("", args)
    if isinstance(args, dict):
        return {k: MODEL_TOKENS.sub("", v) if isinstance(v, str) else v for k, v in args.items()}
    return args

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

def cut_loop(text, times=3, min_len=25):
    """Модель зациклилась (одна и та же фраза ≥ times раз) — текст до второго повтора, иначе None."""
    seen = {}
    for p in re.split(r"(?<=[.!?…])\s+|\n+", text):
        k = " ".join(p.lower().split())
        if len(k) >= min_len:
            seen[k] = seen.get(k, 0) + 1
            if seen[k] >= times:
                first = text.find(p)
                second = text.find(p, first + len(p))
                return text[:second].rstrip() if second > 0 else None
    return None

def chat(messages, think=False, use_tools=True):
    tools = globals()["tools"]() if use_tools else None
    sent = fit_context(messages, tools)
    model = current["model"]
    # num_predict — предел длины ответа (зациклившаяся модель не съест память); свои настройки модели — из списка
    opts = {"temperature": CONFIG["temperature"], "num_ctx": CONFIG["num_ctx"], "num_predict": CONFIG["reply_reserve"],
            **model_options(model)}
    body = {"model": model, "messages": messages, "tools": tools, "stream": False, "think": bool(think), "options": opts}
    if not use_tools:
        body.pop("tools")
    for attempt in range(3):
        try:
            r = http(f"{CONFIG['ollama']}/api/chat", body, timeout=600)
            if r.get("prompt_eval_count"):
                _ctx["chars_per_token"] = max(1.5, min(5.0, sent / r["prompt_eval_count"]))
            msg = r["message"]
            if r.get("done_reason") == "length" and msg.get("tool_calls"):
                # ответ оборвался на пределе длины, а в нём вызов — его данные наверняка неполные, не выполнять
                msg["tool_calls"] = []
                msg["content"] = ((msg.get("content") or "") + "\n\n…(вызов не выполнен: ответ модели оборвался. "
                                  "Делай по частям: файлы — edit_file небольшими кусками.)")
                return msg
            for c in msg.get("tool_calls") or []:
                c["function"]["arguments"] = clean_args(c["function"].get("arguments"))
            if msg.get("content"):
                msg["content"] = MODEL_TOKENS.sub("", re.sub(r"(?m)^\s*thought\s*(?=<)", "", msg["content"]))
            looped = cut_loop(msg.get("content") or "")
            if looped is not None:
                msg["content"] = looped + "\n\n…(модель начала повторяться — ответ обрезан. Попроси иначе или «продолжай».)"
            elif r.get("done_reason") == "length":
                msg["content"] = (msg.get("content") or "") + "\n\n…(ответ слишком длинный — оборван. Напиши «продолжай» — допишу.)"
            return msg
        except urllib.error.HTTPError:
            if attempt == 2:
                raise
            time.sleep(5)  # модель не загрузилась (память ещё занята рисованием) — подождать

FUNCS = {"generate_image": generate_image, **CODE_FUNCS}

# ---------- Длинные задачи: защита от цикла, сокращение старого, отчёт при лимите ----------

READ_TOOLS = {"read_file", "outline", "search_code", "build_check", "code_task_status", "code_task_diff", "list_dir"}

class LoopGuard:
    """Тот же инструмент с теми же аргументами в третий раз за сообщение — цикл: не выполняем, говорим модели.
    После записи в файл чтения считаются заново — файл уже другой."""
    def __init__(self):
        self.seen = {}

    def check(self, name, args):
        if name in ("edit_file", "write_file"):
            self.seen = {k: v for k, v in self.seen.items() if k.split("{", 1)[0] not in READ_TOOLS}
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
