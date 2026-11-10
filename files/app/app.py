# Окно агента в браузере: http://127.0.0.1:8790 (только этот компьютер).
# Защита: сервер слушает только 127.0.0.1; каждый запрос API несёт случайный ключ этого запуска (его знает только
# открытая сервером страница) и проверяется Host/Origin — чужой сайт в браузере не сможет управлять агентом.
# Показывать в окне можно только картинки из папки images.
import hmac, json, mimetypes, os, pathlib, re, secrets, subprocess, sys, threading, time, traceback, urllib.parse, uuid, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = pathlib.Path(__file__).parent
sys.path.insert(0, str(APP))
import agent_lite as agent
import codetools, updater

PORT = int(agent.CONFIG.get("port", 8790))
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
TOKEN = secrets.token_urlsafe(24)
CHATS_DIR = agent.ROOT / "chats"
IMG_ROOT = agent.IMAGES_DIR
IMG_RE = re.compile(r"[A-Za-z]:\\[^\n\"<>|*?:]+?\.png\b", re.I)

state_lock = threading.Condition()
version = 0
status = {"busy": None, "progress": "", "confirm": None}
chats = {}
running_chat = {"chat": None}  # беседа, в которой сейчас работает агент (для вопросов-подтверждений)

def bump():
    global version
    with state_lock:
        version += 1
        state_lock.notify_all()

class Chat:
    def __init__(self, cid, title="Новая беседа", think=False, messages=None, events=None, created=None):
        self.id, self.title, self.think = cid, title, think
        self.created = created or time.time()
        self.model = ""  # модель беседы (список в шапке окна); пусто — модель по умолчанию
        self.messages = messages or [{"role": "system", "content": agent.SYSTEM}]
        self.events = events or []

    def file(self):
        return CHATS_DIR / f"{self.id}.json"

    def save(self):
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.file().with_suffix(".tmp")
        tmp.write_text(json.dumps({"id": self.id, "title": self.title, "think": self.think, "created": self.created,
                                   "model": self.model, "messages": self.messages, "events": self.events},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.file())

    def add(self, kind, text="", **extra):
        ev = {"type": kind, "text": text, "t": time.time(), **extra}
        self.events.append(ev)
        bump()
        return ev

    def brief(self):
        return {"id": self.id, "title": self.title, "created": self.created}

def load_chats():
    if CHATS_DIR.exists():
        for f in CHATS_DIR.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                c = Chat(d["id"], d.get("title", "Беседа"), d.get("think", False), d.get("messages"),
                         d.get("events"), d.get("created"))
                c.model = d.get("model") or ""
                chats[c.id] = c
            except Exception as e:
                print(f"Не прочитал беседу {f.name}: {e}")

def allowed(p):
    try:
        p = pathlib.Path(p).resolve()
        p.relative_to(IMG_ROOT.resolve())
    except (OSError, ValueError):
        return False
    return p.is_file() and p.suffix.lower() == ".png"

def images_in(text):
    out = []
    for m in IMG_RE.finditer(text or ""):
        if allowed(m.group(0)) and m.group(0) not in out:
            out.append(m.group(0))
    return out

_models_cache = {"t": 0.0, "list": []}

def models_list():
    """Установленные модели из списка (опрос Ollama не чаще раза в 5 с — страница спрашивает часто)."""
    if time.time() - _models_cache["t"] > 5:
        _models_cache.update(t=time.time(), list=[{"id": m["id"], "name": m.get("name") or m["id"]} for m in agent.available_models()])
    return _models_cache["list"]

def chat_model(chat):
    ids = [m["id"] for m in models_list()]
    return chat.model if chat.model in ids else agent.default_model()

confirm_answer = threading.Event()
confirm_result = {"ok": False}

def web_confirm(question):
    """Вопрос с кнопками «Выполнить / Отклонить» в беседе; ждём, пока человек нажмёт (или «Стоп»)."""
    chat = running_chat["chat"]
    if agent.stop_flag["stop"] or chat is None:
        return False
    ev = chat.add("confirm", question, answer=None)
    confirm_result["ok"] = False
    confirm_answer.clear()
    status["confirm"] = {"text": question}
    bump()
    confirm_answer.wait()
    status["confirm"] = None
    ev["answer"] = confirm_result["ok"]
    bump()
    return confirm_result["ok"]

agent.confirm = web_confirm

def worker(chat, text):
    def progress(line):
        status["progress"] = line
        bump()
    agent.current["model"] = chat_model(chat)  # модель этой беседы
    running_chat["chat"] = chat
    try:
        if not any(m["role"] == "user" for m in chat.messages):
            t = " ".join(text.split())
            chat.title = t[:48] + ("…" if len(t) > 48 else "")
        if chat.messages and chat.messages[0].get("role") == "system":
            chat.messages[0]["content"] = agent.system_prompt()  # проекты могли подключить после начала беседы
        chat.messages.append({"role": "user", "content": text})
        agent.CURRENT_REQUEST = text  # название задачи кода, если агент начнёт править проект
        guard = agent.LoopGuard()
        steps = agent.max_steps()
        for step in range(steps):
            if agent.stop_flag["stop"]:
                chat.add("info", "Остановлено.")
                break
            progress("думает…" + (f" (шаг {step + 1} из {steps})" if step else ""))
            try:
                msg = agent.chat(chat.messages, chat.think)
            except Exception as e:
                chat.add("error", f"Модель не ответила: {e}. Перезапусти агента ярлыком «Агент».")
                break
            chat.messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
            agent.compact_results(chat.messages)
            calls = msg.get("tool_calls") or []
            if (msg.get("content") or "").strip():
                chat.add("assistant", msg["content"].strip())
            if not calls:
                break
            for c in calls:
                name, args = c["function"]["name"], c["function"].get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                ev = chat.add("tool", "", name=name, args=args, result=None, files=[])
                f = agent.FUNCS.get(name)
                result = guard.check(name, args)  # тот же вызов в третий раз — цикл, не выполняем
                if result is None:
                    try:
                        result = f(**args, progress=progress) if f else f"Нет инструмента {name}"
                    except TypeError as e:
                        result = f"Неверные аргументы: {e}"
                    except Exception as e:
                        result = f"Ошибка: {e}"
                        traceback.print_exc()
                ev["result"] = str(result)
                ev["files"] = images_in(str(result))
                chat.messages.append({"role": "tool", "content": str(result), "tool_name": name})
                bump()
        else:
            progress("шаги кончились — пишет, что сделано и что осталось…")
            chat.add("assistant", agent.wrap_up(chat.messages, chat.think))
            chat.add("info", "Задача не закончена за один раз. Напиши «продолжай», чтобы агент закончил.")
        chat.messages = agent.trim_history(chat.messages)
    except Exception as e:
        chat.add("error", f"Ошибка: {e}")
        traceback.print_exc()
    finally:
        status.update(busy=None, progress="", confirm=None)
        running_chat["chat"] = None
        agent.stop_flag["stop"] = False
        chat.save()
        bump()

def start(chat, text):
    with state_lock:
        if status["busy"]:
            return f"Агент занят в беседе «{chats[status['busy']].title}». Дождись или нажми «Стоп»."
        status.update(busy=chat.id, progress="думает…")
        agent.stop_flag["stop"] = False
    chat.add("user", text)
    threading.Thread(target=worker, args=(chat, text), daemon=True).start()
    return ""

def pick_folder():
    """Окно выбора папки (Windows). Вернуть путь или пусто (отмена)."""
    script = ("Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.Form; $f.TopMost = $true; "
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
              "$d.Description = 'Папка проекта с кодом для агента'; "
              "if ($d.ShowDialog($f) -eq 'OK') { [Console]::OutputEncoding = [Text.Encoding]::UTF8; $d.SelectedPath }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""

def services():
    out = {}
    for name, url in (("ollama", f"{agent.CONFIG['ollama']}/api/tags"), ("comfy", f"{agent.CONFIG['comfy']}/system_stats")):
        try:
            agent.http(url, timeout=2)
            out[name] = True
        except Exception:
            out[name] = False
    return out

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def trusted(self, q=None):
        if self.headers.get("Host", "") not in HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc not in HOSTS:
            return False
        return hmac.compare_digest(self.headers.get("X-Token") or (q or {}).get("t", ""), TOKEN)

    def jbody(self):
        n = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/":
            if self.headers.get("Host", "") not in HOSTS:
                return self.send(403, {"error": "только с этого компьютера"})
            return self.send(200, (APP / "app.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        if not self.trusted(q):
            return self.send(403, {"error": "нет доступа (обнови страницу)"})
        if u.path == "/api/chats":
            return self.send(200, sorted((c.brief() for c in chats.values()), key=lambda c: -c["created"]))
        if u.path == "/api/state":
            v = int(q.get("v") or -1)
            with state_lock:
                if v == version:
                    state_lock.wait(25)
                cur = version
            c = chats.get(q.get("chat", ""))
            return self.send(200, {"v": cur, "status": dict(status), "models": models_list(), "pulling": dict(agent.pulling),
                                   "default_model": agent.default_model(),
                                   "chat": {**c.brief(), "think": c.think, "model": chat_model(c), "events": c.events} if c else None})
        if u.path == "/api/services":
            return self.send(200, services())
        if u.path == "/api/version":
            return self.send(200, {"version": agent.VERSION})
        if u.path == "/api/update/check":
            return self.send(200, updater.check())
        if u.path == "/api/projects":  # подключённые папки проектов и их задачи кода
            tasks = {t["project"]: t for t in codetools.summary_all()}
            return self.send(200, {"projects": [{"name": k, "path": str(v), "task": tasks.get(k),
                                                 "problem": codetools.git_ready(v)} for k, v in codetools.PROJECTS.items()]})
        if u.path == "/file":
            p = pathlib.Path(q.get("path", ""))
            if not allowed(p):
                return self.send(403, {"error": "файл недоступен"})
            return self.send(200, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/png")
        self.send(404, {"error": "нет такого адреса"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self.trusted():
            return self.send(403, {"error": "нет доступа (обнови страницу)"})
        b = self.jbody()
        c = chats.get(b.get("chat", ""))
        if u.path == "/api/chats":
            c = Chat(uuid.uuid4().hex[:12])
            if any(m["id"] == b.get("model") for m in models_list()):  # модель, выбранная до создания беседы
                c.model = b["model"]
            chats[c.id] = c
            c.save()
            bump()
            return self.send(200, c.brief())
        if u.path == "/api/send":
            text = (b.get("text") or "").strip()
            if not c or not text:
                return self.send(400, {"error": "нет беседы или текста"})
            err = start(c, text)
            return self.send(409 if err else 200, {"error": err} if err else {"ok": True})
        if u.path == "/api/stop":
            if status["busy"]:
                agent.stop_flag["stop"] = True
                try:
                    agent.http(f"{agent.CONFIG['comfy']}/interrupt", {}, timeout=5)
                except Exception:
                    pass
                if status["confirm"]:  # ждёт ответа на вопрос — считать «отклонено»
                    confirm_result["ok"] = False
                    confirm_answer.set()
                status["progress"] = "останавливаю…"
                bump()
            return self.send(200, {"ok": True})
        if u.path == "/api/confirm":
            if status["confirm"]:
                confirm_result["ok"] = bool(b.get("ok"))
                confirm_answer.set()
            return self.send(200, {"ok": True})
        if u.path == "/api/projects":  # подключить / отключить папку проекта
            if status["busy"]:
                return self.send(409, {"error": "агент сейчас работает — дождись конца или нажми «Стоп»"})
            if b.get("action") == "pick":  # обычное окно Windows «Выбор папки»
                path = pick_folder()
                if not path:
                    return self.send(200, {"ok": True, "message": ""})
                ok, msg = agent.add_project(path)
            elif b.get("action") == "add":
                ok, msg = agent.add_project(b.get("path", ""), b.get("name", ""))
            elif b.get("action") == "remove":
                ok, msg = agent.remove_project(b.get("name", ""))
            else:
                ok, msg = False, "неизвестное действие"
            return self.send(200 if ok else 400, {"ok": True, "message": msg} if ok else {"error": msg})
        if u.path == "/api/codetask":  # кнопки задачи кода: изменения / закончить / откатить
            action, project = b.get("action"), b.get("project", "")
            if action == "status":
                return self.send(200, {"text": codetools.task_status(project)})
            if status["busy"]:
                return self.send(409, {"error": "агент сейчас работает — дождись конца или нажми «Стоп»"})
            if action == "finish":
                return self.send(200, {"text": codetools.task_finish(project, "закончено кнопкой в окне")})
            if action == "undo":  # подтверждение — на странице
                return self.send(200, {"text": codetools.task_undo(project)})
            return self.send(400, {"error": "неизвестное действие"})
        if u.path in ("/api/think", "/api/rename", "/api/model") and c:
            if u.path == "/api/think":
                c.think = bool(b.get("think"))
            elif u.path == "/api/model":
                if status["busy"] == c.id:
                    return self.send(409, {"error": "беседа сейчас работает — смени модель после ответа"})
                if any(m["id"] == b.get("model") for m in models_list()):
                    c.model = b["model"]
            elif (b.get("title") or "").strip():
                c.title = b["title"].strip()[:80]
            c.save()
            bump()
            return self.send(200, {"ok": True})
        if u.path == "/api/update/apply":
            if status["busy"]:
                return self.send(409, {"error": "агент сейчас работает — дождись конца или нажми «Стоп»"})
            ok, msg = updater.apply()
            if ok:  # ответить странице, потом перезапуститься уже с новыми файлами
                threading.Timer(1.0, restart).start()
            return self.send(200 if ok else 409, {"ok": ok, "message": msg} if ok else {"error": msg})
        if u.path == "/api/folder":  # открыть папку с картинками в проводнике
            IMG_ROOT.mkdir(parents=True, exist_ok=True)
            os.startfile(str(IMG_ROOT))
            return self.send(200, {"ok": True})
        self.send(404, {"error": "нет такого адреса"})

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if not self.trusted():
            return self.send(403, {"error": "нет доступа (обнови страницу)"})
        c = chats.get(q.get("chat", ""))
        if u.path == "/api/chats" and c:
            if status["busy"] == c.id:
                return self.send(409, {"error": "беседа сейчас работает"})
            chats.pop(c.id)
            (CHATS_DIR / "deleted").mkdir(parents=True, exist_ok=True)
            if c.file().exists():
                c.file().replace(CHATS_DIR / "deleted" / c.file().name)
            bump()
            return self.send(200, {"ok": True})
        self.send(404, {"error": "нет такой беседы"})

def restart():
    """После обновления: запустить окно заново (новое чёрное окно) и закрыть это."""
    print("Обновление установлено — перезапускаю окно агента...", flush=True)
    for c in chats.values():
        c.save()
    subprocess.Popen([sys.executable, "-s", str(APP / "app.py"), "--no-browser", "--after-update"],
                     cwd=str(agent.ROOT), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    os._exit(0)

def pull_models():
    """Фон: докачать недостающие модели из списка (после обновления с 1.1.x), страница видит прогресс."""
    def ticker():
        while agent.pulling.get("model") or not done.is_set():
            bump()
            time.sleep(3)
    for _ in range(60):  # Ollama при запуске поднимается не сразу — без неё не понять, чего не хватает
        try:
            agent.http(f"{agent.CONFIG['ollama']}/api/tags", timeout=3)
            break
        except Exception:
            time.sleep(2)
    else:
        return
    done = threading.Event()
    threading.Thread(target=ticker, daemon=True).start()
    try:
        agent.pull_missing()
    finally:
        _models_cache["t"] = 0  # список моделей обновится
        done.set()
        bump()

def main():
    load_chats()
    threading.Thread(target=pull_models, daemon=True).start()
    srv = None
    for attempt in range(20 if "--after-update" in sys.argv else 1):  # после обновления старое окно ещё закрывается
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            time.sleep(0.5)
    if srv is None:
        print("Агент уже запущен — открываю окно.")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{PORT}"
    print(f"Агент работает: {url}\nНе закрывай это окно, пока пользуешься агентом.", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
