# Инструменты для работы с кодом проектов (например, игры Colony):
#   - чтение файла по строкам с номерами, поиск по проекту, оглавление файла (классы и методы);
#   - проверка сборки C# без Unity (dotnet build по копии Assembly-CSharp.csproj);
#   - «задача кода»: перед первой правкой — снимок папки проекта в служебную ветку agent/<дата>-<задача>
#     (без переключения ветки, без коммита в ветку пользователя), потом — изменения, откат, снимок «после».
# Проекты — в config.json: "projects": {"colony": "C:\\Users\\User\\ColonyGame"}.
# Один и тот же файл в полном агенте (настройки из settings.py) и в лайте (agent_lite вызывает setup).
import datetime, json, os, pathlib, re, shutil, subprocess

PROJECTS = {}   # имя → корень проекта
STATE = None    # файл состояния задач кода

def setup(projects, data_dir):
    """Задать проекты и папку данных (можно вызывать снова — например, после подключения новой папки)."""
    global STATE
    PROJECTS.clear()
    PROJECTS.update({k.lower(): pathlib.Path(v) for k, v in (projects or {}).items()})
    STATE = pathlib.Path(data_dir) / "agent_tasks" / "tasks.json"

try:
    import settings  # полный агент
    setup(settings.get("projects"), settings.DATA)
except ImportError:  # лайт: настраивает agent_lite
    setup({}, pathlib.Path(__file__).resolve().parent.parent)
SKIP_DIRS = {".git", "Library", "Temp", "Logs", "obj", "UserSettings", "Build", "Builds", ".vs", ".vscode", "node_modules", "__pycache__"}
TEXT_EXT = {".cs", ".uss", ".uxml", ".json", ".asset", ".txt", ".md", ".py", ".html", ".js", ".css", ".shader", ".ps1", ".bat"}
FORBIDDEN_EXT = {".meta", ".unity", ".prefab"}  # их правит только Unity: сломанный guid/сцену агент не починит
READ_LINES = 150         # строк за один read_file с номерами
READ_CHARS = 7000        # и символов
SEARCH_LIMIT = 60        # совпадений за один поиск
GIT_ENV = {"GIT_AUTHOR_NAME": "nero-agent", "GIT_AUTHOR_EMAIL": "agent@localhost",
           "GIT_COMMITTER_NAME": "nero-agent", "GIT_COMMITTER_EMAIL": "agent@localhost"}

# ---------- Проекты ----------

def project_of(path):
    """(имя, корень) проекта, в котором лежит путь, или (None, None)."""
    try:
        p = pathlib.Path(path).resolve()
    except OSError:
        return None, None
    for name, root in PROJECTS.items():
        try:
            p.relative_to(root.resolve())
            return name, root
        except ValueError:
            continue
    return None, None

def resolve_project(project):
    """Имя проекта или путь внутри него → (имя, корень) или (None, текст ошибки)."""
    if not PROJECTS:
        return None, "Проекты не настроены (config.json → \"projects\")."
    if not project:
        if len(PROJECTS) == 1:
            name = next(iter(PROJECTS))
            return name, PROJECTS[name]
        return None, "Укажи проект: " + ", ".join(PROJECTS)
    if project.lower() in PROJECTS:
        return project.lower(), PROJECTS[project.lower()]
    name, root = project_of(project)
    if name:
        return name, root
    return None, f"Нет проекта «{project}». Есть: " + ", ".join(PROJECTS)

def git_ready(root):
    """Можно ли делать снимки: есть git и папка — репозиторий с хотя бы одним коммитом. Пусто — можно, иначе причина."""
    if not shutil.which("git"):
        return "не установлен Git (git-scm.com) — без него нельзя откатить правки"
    try:
        _git(root, "rev-parse", "HEAD")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return f"папка {root} — не git-репозиторий с коммитом (нужен для отката правок)"
    return ""

def describe_projects():
    return "; ".join(f"{k} — {v}" for k, v in PROJECTS.items()) or "нет"

def forbidden(path):
    """Причина, по которой агенту нельзя писать в этот файл проекта, или пусто."""
    name, _ = project_of(path)
    if name and pathlib.Path(path).suffix.lower() in FORBIDDEN_EXT:
        return f"Файлы {pathlib.Path(path).suffix} в проекте правит только Unity (guid, сцены) — их трогать нельзя."
    return ""

def find_loose(text, old):
    """Кусок old не нашёлся точно: найти его, не обращая внимания на пробелы в конце строк и отступ пустых строк.
    Вернуть точный текст этого места в файле, если совпадение ровно одно, иначе None (и число совпадений)."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    want = [l.rstrip() for l in old.replace("\r\n", "\n").strip("\n").split("\n")]
    if not any(want):
        return None, 0
    have = [l.rstrip() for l in lines]
    hits = [i for i in range(len(have) - len(want) + 1) if have[i:i + len(want)] == want]
    if len(hits) != 1:
        return None, len(hits)
    i = hits[0]
    return nl.join(lines[i:i + len(want)]), 1

# ---------- Чтение, поиск, оглавление ----------

def read_lines(path, start=1, end=0):
    """Строки файла с номерами: start..end (включительно; end 0 — до READ_LINES строк от start)."""
    p = pathlib.Path(path)
    if not p.is_file():
        return f"Файл не найден: {path}"
    lines = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    total = len(lines)
    start = max(1, int(start or 1))
    end = int(end or 0) or start + READ_LINES - 1
    end = min(total, end, start + READ_LINES - 1)
    if start > total:
        return f"В файле всего {total} строк."
    out, size = [], 0
    for i in range(start, end + 1):
        row = f"{i:5}| {lines[i - 1]}"
        size += len(row) + 1
        if size > READ_CHARS:
            end = i - 1
            break
        out.append(row)
    head = f"{p.name}: строки {start}–{end} из {total}"
    tail = f"\n(дальше: read_file с start={end + 1})" if end < total else ""
    return head + "\n" + "\n".join(out) + tail

def _walk(root, exts):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if pathlib.Path(f).suffix.lower() in exts:
                yield pathlib.Path(dirpath) / f

def search_code(pattern, project="", path="", ext="", regex=False):
    """Найти текст в файлах проекта: «путь:строка: текст». Без учёта регистра."""
    if path:
        base = pathlib.Path(path)
        if not base.exists():
            return f"Нет такого пути: {path}"
        name, root = project_of(base)
        root = root or (base if base.is_dir() else base.parent)
    else:
        name, root = resolve_project(project)
        if not name:
            return root
        base = root
    if not pattern:
        return "Пустой запрос."
    try:
        rx = re.compile(pattern if regex else re.escape(pattern), re.I)
    except re.error as e:
        return f"Неверное регулярное выражение: {e}"
    exts = {("." + e.strip().lstrip(".")).lower() for e in ext.split(",") if e.strip()} if ext else TEXT_EXT
    files = [base] if base.is_file() else _walk(base, exts)
    hits, nfiles = [], set()
    for f in files:
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if not rx.search(text):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                rel = os.path.relpath(f, root)
                hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                nfiles.add(rel)
                if len(hits) >= SEARCH_LIMIT:
                    break
        if len(hits) >= SEARCH_LIMIT:
            break
    if not hits:
        return f"Ничего не найдено: «{pattern}» в {base}"
    more = f"\n(показаны первые {SEARCH_LIMIT} — уточни запрос или укажи path/ext)" if len(hits) >= SEARCH_LIMIT else ""
    return f"Корень: {root}\nНайдено в {len(nfiles)} файлах:\n" + "\n".join(hits) + more

# объявления C#: типы и члены (методы, свойства, события, константы); тела и локальные переменные не берём
CS_TYPE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)*(?:(?:public|private|protected|internal|static|sealed|abstract|partial|readonly|unsafe)\s+)*"
                     r"(class|struct|interface|enum|record)\s+(\w+)")
CS_MEMBER = re.compile(r"^\s*(?:\[[^\]]*\]\s*)*((?:(?:public|private|protected|internal|static|virtual|override|abstract|async|"
                       r"sealed|readonly|extern|unsafe|new|const|event)\s+)*)([\w<>\[\],\.\?\(\) ]+?)\s+(\w+)\s*(\(|=>|\{|=|;)")
CS_SKIP = {"return", "if", "else", "for", "foreach", "while", "switch", "case", "new", "throw", "using", "lock", "catch",
           "await", "yield", "var", "goto", "do", "try", "finally", "namespace", "base", "this", "get", "set"}

def outline(path):
    """Оглавление файла: для .cs — классы, методы, свойства с номерами строк; для .uss — селекторы; иначе — заголовки."""
    p = pathlib.Path(path)
    if not p.is_file():
        return f"Файл не найден: {path}"
    lines = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    out = []
    ext = p.suffix.lower()
    depth = 0  # глубина скобок: члены берём только на уровне тела типа (не внутри методов)
    type_depths, opened = [], []
    for i, line in enumerate(lines, 1):
        s = line.strip()
        code = re.sub(r"//.*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])'", "", line)
        if ext == ".cs":
            m = CS_TYPE.match(code)
            if m:
                out.append(f"{i:5}| {'  ' * len(type_depths)}{m.group(1)} {m.group(2)}")
                type_depths.append(depth)
                opened.append(False)
            elif type_depths and depth == type_depths[-1] + 1 and s and not s.startswith(("//", "[", "#", "{", "}")):
                mm = CS_MEMBER.match(code)
                if mm and mm.group(3) not in CS_SKIP and mm.group(2).split()[-1] not in CS_SKIP:
                    kind = mm.group(4)
                    # только методы и свойства: поля и константы занимают место, их видно через read_file
                    if kind in ("(", "=>", "{"):
                        sig = re.sub(r"\s*//.*", "", s).split("{")[0].split("=>")[0].strip().rstrip(";")
                        out.append(f"{i:5}| {'  ' * len(type_depths)}{sig[:150]}")
            depth += code.count("{") - code.count("}")
            if type_depths and depth > type_depths[-1]:
                opened[-1] = True  # открылось тело типа (скобка часто на следующей строке)
            while type_depths and opened[-1] and depth <= type_depths[-1]:
                type_depths.pop()
                opened.pop()
        elif ext == ".uss" or ext == ".css":
            if s.endswith("{") and not s.startswith("/*"):
                out.append(f"{i:5}| {s[:-1].strip()}")
        elif ext == ".py":
            if re.match(r"^(class|def|async def)\s", line) or re.match(r"^\s{4}(def|async def)\s", line):
                out.append(f"{i:5}| {line.rstrip()[:150]}")
        elif s.startswith("#") or re.match(r"^\s*(function|class)\s", line):
            out.append(f"{i:5}| {s[:150]}")
    if not out:
        return f"{p.name}: {len(lines)} строк, объявлений не найдено — читай read_file с start/end."
    text = f"{p.name}: {len(lines)} строк, оглавление (номер строки | объявление):\n" + "\n".join(out)
    if len(text) > READ_CHARS:
        text = text[:READ_CHARS] + "\n...(оглавление обрезано)"
    return text

# ---------- Проверка сборки ----------

def build_check(project=""):
    """Собрать C# проекта без Unity (копия Assembly-CSharp.csproj: все .cs из Assets, кроме папок Editor). Вернуть ошибки."""
    name, root = resolve_project(project)
    if not name:
        return root
    src = root / "Assembly-CSharp.csproj"
    if not src.is_file():
        return f"Нет {src}: открой проект в Unity один раз (он создаёт .csproj) или собирать нечего."
    if not shutil.which("dotnet"):
        return "Не найден dotnet (.NET SDK) — проверка сборки недоступна."
    text = src.read_text(encoding="utf-8-sig")
    # список файлов Unity обновляет сам только при импорте — берём все .cs из Assets, чтобы новые файлы агента тоже собирались
    text = re.sub(r"\s*<Compile Include=\"[^\"]*\"\s*/>", "", text)
    rootw = str(root).rstrip("\\") + "\\"
    def absolute(m):
        val = m.group(2)
        if re.match(r"^[A-Za-z]:\\|^\$\(|^\\\\", val):
            return m.group(0)
        return f'{m.group(1)}"{rootw}{val}"'
    text = re.sub(r'(\bInclude=)"([^"]+)"', absolute, text)  # Import Project="Sdk.props" не трогаем — это SDK
    text = re.sub(r"<HintPath>(?![A-Za-z]:\\)([^<]+)</HintPath>", lambda m: f"<HintPath>{rootw}{m.group(1)}</HintPath>", text)
    compile_items = (f'  <ItemGroup>\n    <Compile Include="{rootw}Assets\\**\\*.cs" '
                     f'Exclude="{rootw}Assets\\**\\Editor\\**\\*.cs" />\n  </ItemGroup>\n')
    text = text.replace("</Project>", compile_items + "</Project>")
    work = root / "Temp" / "AgentCheck"
    work.mkdir(parents=True, exist_ok=True)
    proj = work / "AgentCheck.csproj"
    proj.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(["dotnet", "build", str(proj), "-nologo", "-v", "q", "-clp:NoSummary",
                            f"-p:OutputPath={work}\\bin\\", "-p:AssemblyName=AgentCheck"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, cwd=str(work))
    except subprocess.TimeoutExpired:
        return "Сборка не завершилась за 5 минут."
    out = r.stdout + r.stderr
    errors, seen = [], set()
    for line in out.splitlines():
        m = re.search(r"(.+?)\((\d+),(\d+)\): error (\w+): (.+?)(?: \[[^\]]*\])?$", line.strip())
        if not m:
            continue
        key = (m.group(1), m.group(2), m.group(4))
        if key in seen:
            continue
        seen.add(key)
        rel = os.path.relpath(m.group(1), root) if os.path.isabs(m.group(1)) else m.group(1)
        errors.append(f"{rel}:{m.group(2)}: {m.group(4)} {m.group(5)}")
    if r.returncode == 0:
        done = " Если задача сделана — code_task_finish и отчёт пользователю (файлы, что проверить в Unity)." \
            if _load().get(name, {}).get("active") else ""
        return "Сборка: ошибок нет." + done
    if not errors:
        tail = "\n".join(out.strip().splitlines()[-15:])
        return f"Сборка не удалась (код {r.returncode}), но ошибок C# не найдено:\n{tail}"
    shown = errors[:15]
    more = f"\n...и ещё {len(errors) - 15}" if len(errors) > 15 else ""
    # первым трём ошибкам — сама строка кода и подсказка по коду ошибки (маленькой модели так проще понять, что не так)
    detail = []
    for e in errors[:3]:
        m = re.match(r"(.+?):(\d+): (\w+) (.*)", e)
        if not m:
            continue
        try:
            line = (root / m.group(1)).read_text(encoding="utf-8-sig", errors="replace").splitlines()[int(m.group(2)) - 1].strip()
        except (OSError, IndexError):
            line = ""
        hint = ERROR_HINTS.get(m.group(3), "")
        if m.group(3) in ("CS1955", "CS1061", "CS0019") and re.search(r"\.(Count|Any|Where|Select|Sum|First|Max|Min|OrderBy)\s*\(", line):
            hint = "это LINQ: в начале файла нужен using System.Linq; — или посчитай обычным циклом в методе."
        detail.append(f"  {m.group(1)}:{m.group(2)}: {line[:160]}" + (f"\n    подсказка: {hint}" if hint else ""))
    return (f"Сборка: ошибок {len(errors)}. Исправляй по одной, начиная с первой:\n" + "\n".join(shown) + more +
            ("\nСтроки с ошибками:\n" + "\n".join(detail) if detail else ""))

ERROR_HINTS = {
    "CS0103": "такого имени нет в этом месте — найди объявление (search_code) или проверь using/namespace.",
    "CS0246": "тип не найден — нужен using с его namespace или имя написано не так (search_code).",
    "CS1061": "у этого типа нет такого члена — найди, как он объявлен (search_code), не придумывай имена.",
    "CS1002": "не хватает «;» — смотри строку целиком.",
    "CS1513": "не хватает «}» — посчитай скобки в правке.",
    "CS1525": "недопустимое выражение — после => можно только выражение; для нескольких строк нужен { get { ... } } или метод.",
    "CS0029": "не тот тип — приведи значение или поменяй тип переменной.",
    "CS0161": "не все пути возвращают значение — добавь return.",
    "CS0111": "такой член уже есть — переименуй или не добавляй второй раз.",
    "CS0102": "такое имя в классе уже есть — переименуй.",
}

# ---------- Задачи кода: снимки в служебной ветке ----------

def _git(root, *args, env=None, input_text=None):
    e = dict(os.environ, **GIT_ENV)
    if env:
        e.update(env)
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(root), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=e, input=input_text, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout

def _load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

def _save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

def _snapshot(root, message, parent):
    """Коммит текущего состояния папки (с неотслеживаемыми, без игнорируемых) — во временном индексе:
    ветка, индекс и файлы пользователя не меняются."""
    gitdir = pathlib.Path(_git(root, "rev-parse", "--absolute-git-dir").strip())
    tmp = STATE.parent / "index_tmp"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if (gitdir / "index").exists():
        shutil.copyfile(gitdir / "index", tmp)  # с кэшем дат файлов — add -A не перечитывает весь проект
    elif tmp.exists():
        tmp.unlink()
    env = {"GIT_INDEX_FILE": str(tmp)}
    _git(root, "add", "-A", env=env)
    tree = _git(root, "write-tree", env=env).strip()
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
    return _git(root, *args).strip()

def _slug(title):
    s = re.sub(r"[^\w]+", "-", title.lower(), flags=re.U).strip("-")
    return (s[:30].strip("-") or "task")

def _changes(root, before, after):
    """[(статус A/M/D, путь)] между двумя коммитами."""
    out = _git(root, "diff-tree", "-r", "-z", "--no-renames", "--name-status", before, after)
    parts = [p for p in out.split("\0") if p]
    return [(parts[i][0], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]

def task_start(project="", title="правки"):
    """Начать задачу кода: снимок «до» в ветку agent/... Если задача уже идёт — вернуть её."""
    name, root = resolve_project(project)
    if not name:
        return root
    state = _load()
    cur = state.get(name, {}).get("active")
    if cur:
        return f"Задача уже идёт: «{cur['title']}» (ветка {cur['branch']}). Закончить — code_task_finish."
    why = git_ready(root)
    if why:
        return f"Не удалось сделать снимок проекта: {why}."
    try:
        head = _git(root, "rev-parse", "HEAD").strip()
        before = _snapshot(root, f"агент: до — {title}", head)
        branch = f"agent/{datetime.datetime.now():%Y%m%d-%H%M}-{_slug(title)}"
        _git(root, "branch", "-f", branch, before)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        return f"Не удалось сделать снимок проекта: {e}"
    state.setdefault(name, {})["active"] = {"title": title, "branch": branch, "before": before,
                                             "started": f"{datetime.datetime.now():%Y-%m-%d %H:%M}"}
    _save(state)
    return f"Задача «{title}» начата: снимок «до» в ветке {branch} ({before[:8]}). Откатить всё можно code_task_undo."

def ensure_task(path, title="правки"):
    """Перед записью в файл проекта: начать задачу, если её нет. Вернуть сообщение о начале (или пусто)."""
    name, _ = project_of(path)
    if not name or _load().get(name, {}).get("active"):
        return ""
    return task_start(name, title)

def in_active_task(path):
    name, _ = project_of(path)
    return bool(name and _load().get(name, {}).get("active"))

def _task(name, state):
    t = state.get(name, {})
    return t.get("active") or t.get("last"), bool(t.get("active"))

def task_status(project=""):
    """Что изменено в задаче: файлы относительно снимка «до»."""
    name, root = resolve_project(project)
    if not name:
        return root
    state = _load()
    t, active = _task(name, state)
    if not t:
        return f"Задач кода в проекте {name} ещё не было."
    try:
        now = _snapshot(root, "агент: сейчас", t["before"]) if active else t.get("after", t["before"])
        ch = _changes(root, t["before"], now)
        stat = _git(root, "diff", "--stat=100", t["before"], now).strip()
    except (RuntimeError, OSError) as e:
        return f"Не удалось сравнить: {e}"
    head = (f"Задача «{t['title']}» ({'идёт' if active else 'закончена' + (', ОТКАЧЕНА' if t.get('undone') else '')}), "
            f"ветка {t['branch']}, начата {t['started']}.")
    if not ch:
        return head + "\nИзменений нет."
    names = {"A": "новый", "M": "изменён", "D": "удалён"}
    rows = [f"  {names.get(s, s)}: {p}" for s, p in ch]
    return head + f"\nФайлов: {len(ch)}\n" + "\n".join(rows[:60]) + ("\n" + stat.splitlines()[-1] if stat else "")

def task_diff(project="", file=""):
    """Сами изменения (diff) задачи: одного файла или всех (обрезается)."""
    name, root = resolve_project(project)
    if not name:
        return root
    state = _load()
    t, active = _task(name, state)
    if not t:
        return "Задач кода ещё не было."
    try:
        now = _snapshot(root, "агент: сейчас", t["before"]) if active else t.get("after", t["before"])
        args = ["diff", "-U2", t["before"], now] + (["--", file.replace("\\", "/")] if file else [])
        d = _git(root, *args)
    except (RuntimeError, OSError) as e:
        return f"Не удалось сравнить: {e}"
    if not d.strip():
        return "Изменений нет."
    return d if len(d) <= READ_CHARS else d[:READ_CHARS] + f"\n...(обрезано, всего {len(d)} симв.; укажи file)"

def task_finish(project="", summary=""):
    """Закончить задачу: снимок «после» в ту же ветку. Правки остаются в папке — коммитит пользователь сам."""
    name, root = resolve_project(project)
    if not name:
        return root
    state = _load()
    t = state.get(name, {}).get("active")
    if not t:
        return "Активной задачи нет."
    try:
        after = _snapshot(root, f"агент: после — {t['title']}" + (f"\n\n{summary}" if summary else ""), t["before"])
        _git(root, "branch", "-f", t["branch"], after)
        ch = _changes(root, t["before"], after)
    except (RuntimeError, OSError) as e:
        return f"Не удалось сделать снимок «после»: {e}"
    t["after"] = after
    state[name]["last"] = t
    state[name]["active"] = None
    _save(state)
    return (f"Задача «{t['title']}» закончена: изменено файлов {len(ch)}, снимок «после» в ветке {t['branch']}. "
            "Правки уже в папке проекта; откатить — code_task_undo.")

def task_undo(project="", confirm=None):
    """Вернуть файлы, изменённые в задаче, к снимку «до»; созданные задачей — удалить. С подтверждением."""
    name, root = resolve_project(project)
    if not name:
        return root
    state = _load()
    t, active = _task(name, state)
    if not t:
        return "Откатывать нечего: задач кода ещё не было."
    if t.get("undone") and not active:
        return f"Задача «{t['title']}» уже откачена."
    try:
        now = _snapshot(root, "агент: перед откатом", t["before"])
        ch = _changes(root, t["before"], now)
    except (RuntimeError, OSError) as e:
        return f"Не удалось сравнить: {e}"
    if not ch:
        return "Изменений относительно снимка «до» нет — откатывать нечего."
    # что поменялось уже после конца задачи (правил пользователь) — предупредить отдельно
    later = set()
    if not active and t.get("after"):
        try:
            later = {p for _, p in _changes(root, t["after"], now)}
        except RuntimeError:
            pass
    lines = [f"{'удалить' if s == 'A' else 'вернуть'}: {p}" + ("  ← менялся ПОСЛЕ задачи!" if p in later else "") for s, p in ch]
    q = f"Откатить задачу «{t['title']}» ({len(ch)} файлов):\n" + "\n".join(lines[:40]) + ("\n..." if len(lines) > 40 else "")
    if confirm and not confirm(q):
        return "Пользователь отклонил откат."
    restore = [p for s, p in ch if s != "A"]
    try:
        for i in range(0, len(restore), 50):
            _git(root, "restore", f"--source={t['before']}", "--worktree", "--", *restore[i:i + 50])
    except RuntimeError as e:
        return f"Откат не удался: {e}"
    removed = 0
    for s, p in ch:
        if s == "A":
            f = root / p
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    t["undone"] = True
    if active:
        t["after"] = now
        state[name]["last"] = t
        state[name]["active"] = None
    _save(state)
    return f"Откачено: возвращено файлов {len(restore)}, удалено новых {removed}. Снимок до отката сохранён ({now[:8]})."

def cleanup_branches(project="", keep=10):
    """Удалить старые ветки agent/... (оставить keep последних)."""
    name, root = resolve_project(project)
    if not name:
        return root
    try:
        refs = _git(root, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads/agent/").split()
        state = _load().get(name, {})
        protect = {(state.get("active") or {}).get("branch"), (state.get("last") or {}).get("branch")}
        old = [r for r in refs[keep:] if r not in protect]
        if old:
            _git(root, "branch", "-D", *old)
    except RuntimeError as e:
        return f"Не удалось: {e}"
    return f"Удалено веток: {len(old)}, осталось {len(refs) - len(old)}."

def summary_all():
    """Для окна: по каждому проекту — активная или последняя задача."""
    state = _load()
    out = []
    for name in PROJECTS:
        t, active = _task(name, state)
        if t:
            out.append({"project": name, "title": t["title"], "branch": t["branch"], "active": active,
                        "undone": bool(t.get("undone")), "started": t["started"]})
    return out
