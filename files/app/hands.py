# «Руки» агента: нажимать и печатать в окне РАЗРЕШЁННОЙ программы — только внутри сессии управления.
# Безопасность:
#   - сессия: control_start спрашивает пользователя «разрешить управлять окном X для задачи Y» (на 10 мин без действий);
#   - только окно сессии: перед каждым кликом проверяется, что под курсором это окно, перед печатью — что оно активно;
#   - белый список программ (встроенные + добавленные в окне агента: config.json control_apps), запрет окон банков, почты, паролей, кошельков;
#   - опасные кнопки (удалить, отправить, оплатить...) — отдельное подтверждение;
#   - стоп: кнопка «СТОП» в окошке поверх всех окон, клавиши Pause или Ctrl+Alt+S, пользователь сам двинул мышь.
import ctypes, json, os, pathlib, re, subprocess, threading, time
from ctypes import wintypes
import settings, screen

user32 = ctypes.WinDLL("user32", use_last_error=True)  # свой экземпляр — типы функций не мешают другим модулям
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
HERE = pathlib.Path(__file__).resolve().parent
STOP_FLAG = settings.DATA / "control_stop.flag"
IDLE_LIMIT = 600            # сессия закрывается после 10 минут без действий
confirm = lambda q: False   # подменяет agent
on_stop = None              # подменяет окно: остановить работу агента

DEFAULT_ALLOW = ["notepad.exe", "mspaint.exe", "explorer.exe", "winword.exe", "excel.exe", "powerpnt.exe",
                 "title:Калькулятор", "title:Calculator"]
DENY_TITLE = re.compile(r"банк|bank|сбер|тинькофф|т-банк|втб|альфа|gazprom|газпром|пароль|password|keepass|bitwarden|1password|"
                        r"почта|mail|gmail|outlook|yandex.?mail|кошел|wallet|crypto|крипт|binance|paypal|qiwi|юmoney|госуслуг|"
                        r"оплат|checkout|платеж|платёж", re.I)
DANGER = re.compile(r"удал|delete|remove|отправ|send|оплат|pay|купить|buy|заказ|order|подтверд|confirm|форматир|format|"
                    r"очистить корзину|empty recycle|стереть|erase|перезаписать|replace|выйти|sign out|log ?out", re.I)

session = {}  # hwnd, title, exe, task, until, cursor (где курсор оставил агент), indicator (процесс)

# ---------- Ввод (SendInput) ----------

ULONG_PTR = ctypes.c_size_t

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]

class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]

def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

def _mouse(flags, data=0):
    i = INPUT(type=0)
    i.u.mi = MOUSEINPUT(0, 0, data, flags, 0, 0)
    return i

def _click_at(x, y, double=False, right=False):
    """Довести мышь до точки настоящим движением (приложения видят наведение) и щёлкнуть."""
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)    # виртуальный экран (все мониторы)
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    ax = int((x - vx) * 65535 / max(1, vw - 1))
    ay = int((y - vy) * 65535 / max(1, vh - 1))
    i = INPUT(type=0)
    i.u.mi = MOUSEINPUT(ax, ay, 0, 0x0001 | 0x8000 | 0x4000, 0, 0)        # MOVE | ABSOLUTE | VIRTUALDESK
    _send(i)
    user32.SetCursorPos(x, y)  # точно в пиксель (абсолютные координаты округляются)
    time.sleep(0.12)
    down, up = (0x0008, 0x0010) if right else (0x0002, 0x0004)
    for _ in range(2 if double else 1):
        _send(_mouse(down))
        time.sleep(0.03)
        _send(_mouse(up))
        time.sleep(0.08)

def _key(vk=0, scan=0, flags=0):
    i = INPUT(type=1)
    i.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return i

KEYUP, UNICODE = 0x0002, 0x0004
VK = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
      "space": 0x20, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22, "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "insert": 0x2D,
      **{f"f{i}": 0x6F + i for i in range(1, 13)}}
FORBIDDEN_KEYS = {"win", "lwin", "rwin", "windows"}

def _vk(name):
    n = name.strip().lower()
    if n in VK:
        return VK[n]
    if len(n) == 1 and (n.isalnum()):
        return ord(n.upper()) if n.isascii() else None
    return None

# ---------- Остановка ----------

def _stop_requested():
    return STOP_FLAG.exists()

def _end(reason):
    proc = session.get("indicator")
    if proc and proc.poll() is None:
        proc.terminate()
    title = session.get("title", "")
    session.clear()
    try:
        STOP_FLAG.unlink()
    except OSError:
        pass
    return f"Управление окном «{title}» остановлено: {reason}." if title else "Сессии управления нет."

def _hotkey_thread():
    """Глобальные клавиши остановки: Pause и Ctrl+Alt+S → файл-флаг (поток со своим циклом сообщений)."""
    ok1 = user32.RegisterHotKey(None, 1, 0x4000, 0x13)            # Pause (MOD_NOREPEAT)
    ok2 = user32.RegisterHotKey(None, 2, 0x4000 | 0x0002 | 0x0001, 0x53)  # Ctrl+Alt+S
    if not ok1 and not ok2:
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312 and session:  # WM_HOTKEY
            STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
            STOP_FLAG.write_text("hotkey", encoding="utf-8")
            if on_stop:
                try:
                    on_stop()
                except Exception:
                    pass

_hotkeys_started = False

def _ensure_hotkeys():
    global _hotkeys_started
    if not _hotkeys_started:
        _hotkeys_started = True
        threading.Thread(target=_hotkey_thread, daemon=True).start()

def _check():
    """Перед каждым действием: сессия жива, стоп не нажат, мышь не трогали, окно на месте. Пусто — можно."""
    if not session:
        return "Нет сессии управления: сначала control_start (пользователь разрешает управление окном)."
    if _stop_requested():
        return _end("пользователь нажал «Стоп»") + " Не продолжай — спроси пользователя."
    if time.time() > session["until"]:
        return _end("10 минут без действий") + " Нужна новая control_start."
    if not user32.IsWindow(session["hwnd"]):
        return _end("окно закрыто")
    if session.get("cursor"):
        p = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        cx, cy = session["cursor"]
        if abs(p.x - cx) > 4 or abs(p.y - cy) > 4:
            return _end("пользователь сам двинул мышь") + " Не продолжай — спроси пользователя."
    return ""

def _focus():
    """Вывести окно сессии на передний план. Вернуть пусто или причину."""
    hwnd = session["hwnd"]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if not _is_session_window(user32.GetForegroundWindow()):
        # любое нажатие снимает запрет Windows на смену активного окна; F24 (её нет на клавиатурах) — не Alt:
        # одиночный Alt в современных приложениях включает подсказки клавиш, и следующий клик тратится на их отмену
        _send(_key(0x87), _key(0x87, flags=KEYUP))
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.25)
    if not _is_session_window(user32.GetForegroundWindow()):  # диалоги этого окна тоже его
        return "не удалось сделать окно активным (Windows не дала) — щёлкни по нему сам и повтори"
    return ""

def _same(a, b):
    return (a or 0) == (b or 0)

def _is_session_window(h):
    """Окно — окно сессии или его диалог (владелец — окно сессии)."""
    return _same(h, session["hwnd"]) or _same(user32.GetAncestor(h, 3), session["hwnd"])

def _owner_ok(hwnd_at):
    """Окно под точкой — окно сессии, его дочернее или его диалог."""
    root = user32.GetAncestor(hwnd_at, 2)  # GA_ROOT
    if _is_session_window(root):
        return True
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    return pid.value == session["pid"]  # всплывающие меню той же программы

def _state():
    """Коротко, что сейчас видно в окне (тексты и поля) — чтобы модель видела результат действия сразу."""
    if not session:
        return ""
    time.sleep(0.3)
    items, _ = screen.read_elements(session["hwnd"], depth=8, limit=250)
    seen, rows = set(), []
    for e in items or []:
        if e.get("type") not in ("Text", "Edit", "Document", "ComboBox", "StatusBar"):
            continue
        t = (e.get("value") or e.get("name") or "").strip().replace("\n", " ")
        if t and t not in seen and len(rows) < 6:
            seen.add(t)
            rows.append(t[:90])
    if not rows:  # программа не сообщает элементы — текст окна распознаётся со снимка (Windows OCR)
        from PIL import ImageGrab
        l, t, r, b = screen._window_rect(session["hwnd"])
        if r - l > 20 and b - t > 20:
            lines = screen.ocr(ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)) or []
            rows = [ln["text"][:90] for ln in lines if len(ln.get("text", "").strip()) > 1][:10]
            if rows:
                rows[0] = "(распознано со снимка) " + rows[0]
    title = ""
    n = user32.GetWindowTextLengthW(session["hwnd"])
    if n:
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(session["hwnd"], buf, n + 1)
        title = buf.value
    return ("\nСейчас в окне" + (f" «{title}»" if title else "") + ": " + " | ".join(rows)) if rows else ""

def _touch():
    session["until"] = time.time() + IDLE_LIMIT
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    session["cursor"] = (p.x, p.y)

# ---------- Инструменты ----------

# ---------- Список программ, которыми можно управлять (встроенные + добавленные пользователем в окне) ----------

DEFAULT_NAMES = {"notepad.exe": "Блокнот", "mspaint.exe": "Paint", "explorer.exe": "Проводник", "winword.exe": "Word",
                 "excel.exe": "Excel", "powerpnt.exe": "PowerPoint", "title:Калькулятор": "Калькулятор",
                 "title:Calculator": "Калькулятор"}
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "browser.exe", "yandex.exe", "brave.exe", "vivaldi.exe"}
NEVER = {"keepass.exe", "keepassxc.exe", "bitwarden.exe", "1password.exe", "lastpass.exe", "cmd.exe", "powershell.exe",
         "pwsh.exe", "windowsterminal.exe", "wt.exe", "regedit.exe", "taskmgr.exe", "mmc.exe", "python.exe", "pythonw.exe"}

def _config_path():
    return pathlib.Path(settings.HERE) / "config.json"

def user_apps():
    """Программы, добавленные пользователем: [{exe, name, path}] (config.json control_apps; старое control_allow тоже)."""
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    apps = [a for a in data.get("control_apps") or [] if isinstance(a, dict) and a.get("exe")]
    for a in data.get("control_allow") or []:  # старый формат — просто имена
        if isinstance(a, str) and not any(x["exe"].lower() == a.lower() for x in apps):
            apps.append({"exe": a, "name": a, "path": ""})
    return apps

def _save_apps(apps):
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    data["control_apps"] = apps
    data.pop("control_allow", None)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

def add_app(exe, name="", path=""):
    """Разрешить программу (по имени .exe). Вернуть (ok, сообщение)."""
    exe = os.path.basename(str(exe or "").strip())
    if not exe.lower().endswith(".exe"):
        return False, "Нужно имя программы .exe"
    if exe.lower() in NEVER:
        return False, f"{exe} нельзя отдать агенту: это командная строка, менеджер паролей или системная программа."
    apps = [a for a in user_apps() if a["exe"].lower() != exe.lower()]
    apps.append({"exe": exe, "name": (name or exe)[:60], "path": str(path or "")})
    _save_apps(apps)
    return True, f"Разрешено: {name or exe}"

def remove_app(exe):
    apps = user_apps()
    left = [a for a in apps if a["exe"].lower() != str(exe).lower()]
    if len(left) == len(apps):
        return False, "Такой программы в добавленных нет (встроенные не убираются)."
    _save_apps(left)
    return True, f"Убрано: {exe}"

def apps_overview():
    """Для окна: встроенные, добавленные и открытые сейчас программы (разрешена ли, запрещена ли)."""
    added = user_apps()
    allowed = {k.lower() for k in DEFAULT_NAMES if not k.startswith("title:")} | {a["exe"].lower() for a in added}
    seen, opened = set(), []
    for w in screen.windows():
        exe = w["exe"]
        if not exe or exe.lower() in seen or exe.lower() in ("applicationframehost.exe", "rvrvpngui.exe"):
            continue
        seen.add(exe.lower())
        opened.append({"exe": exe, "title": w["title"][:80], "path": w.get("path", ""), "allowed": exe.lower() in allowed,
                       "never": exe.lower() in NEVER, "browser": exe.lower() in BROWSERS})
    builtin = [{"exe": k, "name": v} for k, v in DEFAULT_NAMES.items() if k != "title:Calculator"]
    return {"builtin": builtin, "added": added, "open": opened}

def _allowed(w):
    allow = [a["exe"].lower() for a in user_apps()] + [a.lower() for a in DEFAULT_NAMES]
    exe, title = w["exe"].lower(), w["title"].lower()
    if exe in NEVER:
        return False
    return exe in allow or any(a.startswith("title:") and a[6:].lower() in title for a in allow)

def control_start(window, task="", **_):
    """Начать сессию управления окном (с подтверждением пользователя)."""
    if session:
        if _stop_requested():
            _end("нажат «Стоп»")
        else:
            return f"Сессия уже идёт: окно «{session['title']}». Закончить — control_stop."
    w = screen.find_window(window)
    if not w:
        return f"Окно «{window}» не найдено. Открой программу (control_open) или посмотри list_windows."
    if DENY_TITLE.search(w["title"]):
        return f"Отказано: окно «{w['title']}» похоже на банк, почту, пароли или кошелёк — управлять такими нельзя."
    if not _allowed(w):
        names = sorted({v for v in DEFAULT_NAMES.values()} | {a["name"] for a in user_apps()})
        return (f"Программа {w['exe']} не в списке разрешённых для управления (разрешены: {', '.join(names)}). "
                "Скажи пользователю: добавить её можно в окне агента — «Программы для управления» → «Добавить из открытых окон».")
    if not confirm(f"Разрешить агенту управлять окном «{w['title']}» ({w['exe']})?\nЗадача: {task or '—'}\n"
                   "Мышь и клавиатура будут заняты. Стоп: кнопка «СТОП» сверху экрана, Pause или Ctrl+Alt+S, или просто двинь мышь."):
        return "Пользователь не разрешил управление."
    try:
        STOP_FLAG.unlink()
    except OSError:
        pass
    _ensure_hotkeys()
    ind = None
    try:
        ind = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File",
                                str(HERE / "indicator.ps1"), "-Text", w["title"][:50], "-Flag", str(STOP_FLAG)],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        pass
    session.update(hwnd=w["hwnd"], title=w["title"], exe=w["exe"], pid=w["pid"], task=task, indicator=ind, cursor=None,
                   until=time.time() + IDLE_LIMIT)
    return (f"Управление окном «{w['title']}» разрешено. Действуй: control_click (по имени элемента из ui_elements), "
            "control_type, control_keys. Закончил — control_stop.")

def _find_element(name):
    items, err = screen.read_elements(session["hwnd"], depth=8, limit=400)
    if items is None:
        return None, f"не прочитать элементы: {err}"
    q = name.strip().lower()
    exact = [e for e in items if (e.get("name") or "").strip().lower() == q]
    part = [e for e in items if q in (e.get("name") or "").lower()]
    hits = exact or part
    if not hits:
        names = ", ".join(sorted({e.get('name') for e in items if e.get('name')})[:25])
        return None, (f"элемента «{name}» нет. Есть: {names}. Если нужного нет в списке — control_click_visual с описанием места"
                      if names else f"программа не сообщает свои элементы — нажимай по снимку: control_click_visual с описанием места")
    return hits[0], ""

def control_click(name="", double=False, right=False, **_):
    """Щёлкнуть по элементу окна сессии (по имени из ui_elements)."""
    bad = _check()
    if bad:
        return bad
    if not name:
        return "Укажи name — имя элемента (кнопки, пункта меню, поля) из ui_elements."
    el, err = _find_element(name)
    if not el:
        return "Не нажато: " + err
    if DANGER.search(el.get("name") or "") and not confirm(
            f"Агент хочет нажать «{el.get('name')}» в окне «{session['title']}». Это может быть необратимо. Нажать?"):
        return "Пользователь не разрешил это нажатие."
    if not el.get("enabled", True):
        return f"Элемент «{el.get('name')}» неактивен — нажать нельзя."
    bad = _focus()
    if bad:
        return "Не нажато: " + bad
    x, y = int(el["x"]), int(el["y"])
    at = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not _owner_ok(at):
        return "Не нажато: в этом месте сейчас другое окно (что-то перекрыло) — посмотри look_at_screen."
    _click_at(x, y, double, right)
    _touch()
    time.sleep(0.4)
    return f"Нажато: {el.get('type')} «{el.get('name')}»." + _state()

GROUND_PROMPT = ("Снимок окна программы. Найди на нём: {what}. Ответь ТОЛЬКО координатами центра этого места x,y "
                 "в шкале от 0 до 1000 по ширине и высоте снимка. Если такого на снимке нет — ответь одним словом НЕТ.")

def grounding_model():
    """Модель для поиска места на снимке: config grounding_model, по умолчанию Qwen 3.5 (точно находит элементы; Gemma — нет)."""
    m = settings.get("grounding_model") or "qwen3.5:9b"
    return m if screen.has_vision(m) else ""

VERIFY_PROMPT = ("На снимке маленьким красным кругом отмечено место. Центр круга — внутри «{what}» (на самом элементе, не рядом и не на соседнем)? "
                 "Ответь одним словом: ДА или НЕТ.")

def _ask_xy(model, img, what):
    """Точка (x, y) в пикселях img или None (модель ответила НЕТ / без чисел)."""
    a = screen.ask_image(model, img, GROUND_PROMPT.format(what=what), timeout=180)
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", a)[:2]]
    if len(nums) < 2 or not all(0 <= v <= 1000 for v in nums):
        return None
    return nums[0] * img.width / 1000, nums[1] * img.height / 1000

def _locate(model, img, target, label=""):
    """Найти место: по надписи (точнее всего) или описанию — грубо по окну → точно по увеличенному вырезу → проверка
    «метка на месте?». Вернуть (x, y) в пикселях снимка или строку-причину, почему не нажимаем."""
    if not label:  # надпись в кавычках в описании: «Правка», "OK"
        m = re.search(r"[«\"']([^»\"']{1,40})[»\"']", target)
        label = m.group(1) if m else ""
    if label:  # надпись — точнее всего находит распознавание текста Windows (рамка слова до пикселя)
        hits = screen.find_text(img, label)
        if hits:
            if len(hits) == 1:
                return hits[0][0], hits[0][1]
            guess = _ask_xy(model, img, target)  # несколько одинаковых надписей — какую имел в виду, подскажет модель
            if guess:
                best = min(hits, key=lambda h: (h[0] - guess[0]) ** 2 + (h[1] - guess[1]) ** 2)
                return best[0], best[1]
            return hits[0][0], hits[0][1]
    queries = ([f"надпись «{label}»"] if label else []) + [target]
    why = ""
    for q in queries:
        found = _locate_one(model, img, q, target)
        if not isinstance(found, str):
            return found
        why = found
    return why

def _locate_one(model, img, query, target):
    from PIL import ImageDraw
    coarse = _ask_xy(model, img, query)
    if coarse is None:
        return f"«{target}» на снимке окна не найдено. Посмотри look_at_screen и опиши иначе."
    cw, ch = min(480, img.width), min(320, img.height)
    left = int(min(max(0, coarse[0] - cw / 2), img.width - cw))
    top = int(min(max(0, coarse[1] - ch / 2), img.height - ch))
    crop = img.crop((left, top, left + cw, top + ch)).resize((cw * 2, ch * 2))
    fine = _ask_xy(model, crop, query)
    if fine is None:
        return (f"не уверен, где «{target}» (при увеличении на этом месте его нет) — не нажимал. "
                "Опиши точнее (текст на кнопке, где в окне) или используй клавиши.")
    mark = crop.convert("RGB")
    d = ImageDraw.Draw(mark)
    fx, fy = fine
    d.ellipse((fx - 9, fy - 9, fx + 9, fy + 9), outline=(255, 0, 0), width=3)  # мелкая метка — «да» не за соседа
    verdict = screen.ask_image(model, mark, VERIFY_PROMPT.format(what=target), timeout=120)
    if not re.search(r"\bда\b|\byes\b", verdict, re.I):
        return f"нашёл место, но проверка не подтвердила, что это «{target}» — не нажимал. Опиши иначе или используй клавиши."
    return left + fx / 2, top + fy / 2

def control_click_visual(target, text="", double=False, right=False, **_):
    """Щёлкнуть по месту, найденному на снимке окна по описанию (для программ без списка элементов)."""
    bad = _check()
    if bad:
        return bad
    if not target:
        return "Опиши, куда нажать: «меню Файл», «кнопка ОК внизу справа», «поле поиска»."
    model = grounding_model()
    if not model:
        return "Нет модели для поиска на снимке (нужна qwen3.5:9b или config grounding_model с поддержкой картинок)."
    if DANGER.search(target) and not confirm(
            f"Агент хочет нажать «{target}» в окне «{session['title']}». Это может быть необратимо. Нажать?"):
        return "Пользователь не разрешил это нажатие."
    bad = _focus()
    if bad:
        return "Не нажато: " + bad
    time.sleep(0.2)
    from PIL import ImageGrab
    l, t, r, b = screen._window_rect(session["hwnd"])
    if r - l < 20 or b - t < 20:
        return "Не нажато: окно сессии не видно на экране."
    img, off = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True), (l, t)
    try:
        found = _locate(model, img, target, text)
    except Exception as e:
        return f"Не нажато: модель {model} не ответила: {e}"
    if isinstance(found, str):
        return "Не нажато: " + found
    x, y = int(off[0] + found[0]), int(off[1] + found[1])
    l, t, r, b = screen._window_rect(session["hwnd"])
    at = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not (l <= x < r and t <= y < b) or not _owner_ok(at):
        return f"Не нажато: найденное место ({x},{y}) вне окна «{session['title']}» или перекрыто — опиши точнее."
    # снимок с меткой — пользователь видит в чате, куда нажали
    from PIL import ImageDraw
    mark = img.convert("RGB")
    d = ImageDraw.Draw(mark)
    mx, my = x - off[0], y - off[1]
    for rad, wdt in ((14, 3), (3, 3)):
        d.ellipse((mx - rad, my - rad, mx + rad, my + rad), outline=(255, 0, 0), width=wdt)
    shot = screen._save(mark, "click")
    bad = _check() or _focus()  # пока модель искала место (секунды), могли нажать «Стоп» или сменить окно
    if bad:
        return "Не нажато: " + bad
    if not _owner_ok(user32.WindowFromPoint(wintypes.POINT(x, y))):
        return "Не нажато: место перекрыло другое окно."
    _click_at(x, y, double, right)
    _touch()
    return (f"Нажато по снимку: «{target}» в точке ({x},{y}) (снимок с меткой: {shot}). "
            "Нажатие по снимку может промахнуться — проверь результат." + _state())

def control_type(text, **_):
    """Напечатать текст в активное поле окна сессии."""
    bad = _check()
    if bad:
        return bad
    if not text:
        return "Пустой текст."
    if len(text) > 5000:
        return "Слишком длинный текст (больше 5000 символов) — печатай частями."
    bad = _focus()
    if bad:
        return "Не напечатано: " + bad
    for i, ch in enumerate(text.replace("\r\n", "\n")):
        if _stop_requested():
            return _end("пользователь нажал «Стоп»") + f" Успел напечатать {i} символов."
        if not _is_session_window(user32.GetForegroundWindow()):
            return _end("активным стало другое окно") + f" Успел напечатать {i} символов."
        if ch == "\n":
            _send(_key(0x0D), _key(0x0D, flags=KEYUP))
        else:
            code = ord(ch)
            if code > 0xFFFF:  # символ вне BMP — двумя половинами
                code -= 0x10000
                parts = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
            else:
                parts = [code]
            for c in parts:
                _send(_key(0, c, UNICODE), _key(0, c, UNICODE | KEYUP))
        time.sleep(0.008)
    _touch()
    return f"Напечатано {len(text)} символов в «{session['title']}»." + _state()

def control_keys(keys, **_):
    """Нажать клавиши или сочетание: enter, tab, esc, ctrl+s, alt+f4, ctrl+a, f5..."""
    bad = _check()
    if bad:
        return bad
    out = []
    combos = [c for c in re.split(r"[,\s]+", keys.strip()) if c]
    singles = [c for c in combos if "+" not in c and len(c) == 1]
    if len(singles) > 2:  # буквы по одной — это набор текста; клавишами он зависит от раскладки и выходит мусор
        return "Для текста используй control_type (печатает любые символы). control_keys — для клавиш и сочетаний: enter, tab, ctrl+s."
    for combo in combos:
        if not combo:
            continue
        parts = [p for p in combo.lower().split("+") if p]
        if any(p in FORBIDDEN_KEYS for p in parts) or set(parts) >= {"ctrl", "alt", "delete"} or set(parts) >= {"ctrl", "shift", "esc"}:
            return f"Сочетание «{combo}» запрещено."
        vks = [_vk(p) for p in parts]
        if None in vks:
            return f"Не знаю клавишу в «{combo}». Можно: буквы/цифры, " + ", ".join(sorted(VK))
        if "alt" in parts and "f4" in parts and not confirm(f"Агент хочет закрыть окно «{session['title']}» (Alt+F4). Разрешить?"):
            return "Пользователь не разрешил закрыть окно."
        bad = _focus()
        if bad:
            return "Не нажато: " + bad
        _send(*[_key(v) for v in vks], *[_key(v, flags=KEYUP) for v in reversed(vks)])
        out.append(combo)
        time.sleep(0.15)
    _touch()
    return "Нажато: " + ", ".join(out) + _state()

def control_scroll(amount=-3, **_):
    """Прокрутить колесом над окном сессии: минус — вниз, плюс — вверх (в «щелчках»)."""
    bad = _check()
    if bad:
        return bad
    bad = _focus()
    if bad:
        return "Не прокручено: " + bad
    l, t, r, b = screen._window_rect(session["hwnd"])
    user32.SetCursorPos((l + r) // 2, (t + b) // 2)
    _send(_mouse(0x0800, ctypes.c_uint32(int(amount) * 120).value))
    _touch()
    return f"Прокручено на {amount}."

def control_open(program, **_):
    """Запустить разрешённую программу (блокнот, paint, word, excel, калькулятор, проводник)."""
    names = {"блокнот": "notepad.exe", "notepad": "notepad.exe", "paint": "mspaint.exe", "пэйнт": "mspaint.exe",
             "word": "winword.exe", "ворд": "winword.exe", "excel": "excel.exe", "эксель": "excel.exe",
             "калькулятор": "calc.exe", "calc": "calc.exe", "проводник": "explorer.exe", "explorer": "explorer.exe",
             "powerpoint": "powerpnt.exe"}
    want = program.strip().lower()
    exe = names.get(want, want)
    added = user_apps()
    # добавленная пользователем программа — по имени .exe или подписи (запускается по сохранённому пути)
    hit = next((a for a in added if want in (a["exe"].lower(), a["exe"].lower()[:-4], a["name"].lower())), None)
    if hit:
        exe = hit["exe"].lower()
    allowed = {"notepad.exe", "mspaint.exe", "winword.exe", "excel.exe", "calc.exe", "explorer.exe", "powerpnt.exe"} | \
              {a["exe"].lower() for a in added}
    if not hit and re.search(r"chrome|firefox|edge|opera|yandex|browser|браузер|google|гугл|яндекс|http|www\.|\.(com|ru|org|net)\b", exe):
        return ("Обычный браузер пользователя не в списке разрешённых (там почта, банки, пароли). Для интернета есть свой браузер "
                "агента: browser_search (Google), browser_open (адрес) — или web_search / web_read. "
                "Показать страницу пользователю в его браузере — open_url.")
    if not exe.endswith(".exe"):
        exe += ".exe"
    if exe not in allowed:
        names_ok = sorted({v for v in DEFAULT_NAMES.values()} | {a["name"] for a in added})
        return (f"Запускать можно только: {', '.join(names_ok)}. Другую программу пользователь добавляет в окне агента "
                "(«Программы для управления»).")
    target = hit["path"] if hit and hit.get("path") and os.path.isfile(hit["path"]) else exe
    try:
        os.startfile(target)
    except OSError as e:
        return f"Не запустилось: {e}" + (" (программу проще открыть самому — потом control_start)" if not hit or not hit.get("path") else "")
    time.sleep(2.5)
    return f"Запущено: {exe}. Дальше — control_start с заголовком его окна (list_windows)."

def control_stop(**_):
    return _end("задача закончена")
