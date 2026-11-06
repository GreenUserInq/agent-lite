# Обновление окна агента с GitHub (репозиторий автора): update.json — версия, что нового, файлы с SHA-256.
# Обновляются только файлы окна (app\, start.bat, uninstall.*). Беседы, картинки, модели, ComfyUI и Ollama не трогаются.
# Безопасность: только HTTPS с адреса update_url; каждый файл сверяется по SHA-256 из update.json; пути — только внутри
# папки агента и только знакомые типы файлов; если хоть один файл не сошёлся — ничего не меняется.
# Прежняя версия кладётся в update_backup\<версия>\ — откат: скопировать файлы оттуда обратно.
import hashlib, json, pathlib, re, shutil, time, urllib.request

import agent_lite as agent

ALLOWED = re.compile(r"^(app/[\w.-]+\.(py|html|ps1|css|js|txt|json)|start\.bat|uninstall\.(bat|ps1)|README\.txt)$")
NO_TOUCH = {"app/config.json"}  # свои настройки человека не перезаписываем

def _get(url, timeout=30):
    if not url.startswith("https://"):
        raise ValueError("адрес обновлений должен начинаться с https://")
    req = urllib.request.Request(url, headers={"User-Agent": "agent-lite-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(20 * 1024 * 1024)

def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)

def manifest():
    url = agent.CONFIG.get("update_url") or ""
    if not url:
        raise ValueError("адрес обновлений не задан")
    m = json.loads(_get(f"{url}?t={int(time.time())}", timeout=15))
    if not isinstance(m.get("files"), list) or not m.get("version"):
        raise ValueError("файл обновления странный (нет версии или списка файлов)")
    return m, url.rsplit("/", 1)[0] + "/"

def check():
    """{current, latest, notes, available} или {error}."""
    try:
        m, _ = manifest()
    except Exception as e:
        return {"current": agent.VERSION, "error": f"не удалось проверить обновления: {e}"}
    return {"current": agent.VERSION, "latest": m["version"], "notes": m.get("notes", ""),
            "available": vtuple(m["version"]) > vtuple(agent.VERSION)}

def apply():
    """Скачать и проверить все файлы, сохранить старые в update_backup, заменить. Возвращает (ok, сообщение)."""
    try:
        m, base = manifest()
    except Exception as e:
        return False, f"не удалось получить обновление: {e}"
    if vtuple(m["version"]) <= vtuple(agent.VERSION):
        return False, f"уже стоит последняя версия ({agent.VERSION})"
    root = agent.ROOT.resolve()
    staged = []
    for f in m["files"]:
        path, repo, sha = f.get("path", ""), f.get("repo", ""), (f.get("sha256") or "").lower()
        if not ALLOWED.match(path) or not re.fullmatch(r"[\w./-]+", repo) or ".." in repo or len(sha) != 64:
            return False, f"в обновлении недопустимый файл «{path}» — обновление отменено"
        if path in NO_TOUCH:
            continue
        try:
            data = _get(base + repo)
        except Exception as e:
            return False, f"не скачался {path}: {e}. Попробуй позже — ничего не изменено"
        if hashlib.sha256(data).hexdigest() != sha:
            return False, (f"файл {path} не совпал с контрольной суммой (возможно, GitHub ещё не обновил кэш — "
                           "попробуй через пару минут). Ничего не изменено")
        dst = (root / path).resolve()
        if root not in dst.parents:
            return False, f"путь {path} вне папки агента — обновление отменено"
        staged.append((dst, data))
    backup = root / "update_backup" / agent.VERSION
    for dst, _ in staged:
        if dst.exists():
            b = backup / dst.relative_to(root)
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, b)
    for dst, data in staged:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".new")
        tmp.write_bytes(data)
        tmp.replace(dst)
    return True, f"обновлено до версии {m['version']} (файлов: {len(staged)}; прежняя версия — в {backup})"
