# Чтение PDF для агента (общий модуль полного агента и лайта): текст по страницам, частями.
# Библиотека — pypdf (чистый Python). Нет её — ставится сама в фоне (pip в том Python, которым запущен агент).
# PDF-скан (картинки без текстового слоя) прочитать нельзя — агент так и скажет.
import io, os, pathlib, re, subprocess, sys, threading

READ_CHARS = 7000          # столько символов за один вызов видит модель
PYPDF = "pypdf==5.1.0"

pypdf = None
INSTALL_STATE = ""         # "" / "ставится" / "ошибка: …"
_lock = threading.Lock()

def _import():
    global pypdf
    try:
        import importlib
        importlib.invalidate_caches()
        import pypdf as m
        pypdf = m
    except ImportError:
        try:  # окно запускает Python с -s — зависимости иногда в пользовательской папке библиотек
            import site
            user_site = site.getusersitepackages()
            if user_site and user_site not in sys.path and os.path.isdir(user_site):
                sys.path.append(user_site)
            import pypdf as m
            pypdf = m
        except ImportError:
            pypdf = None
    return pypdf

def _install():
    global INSTALL_STATE
    try:
        r = subprocess.run([sys.executable, "-s", "-m", "pip", "install", "--no-warn-script-location", "-q", PYPDF],
                           capture_output=True, text=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        state = "" if r.returncode == 0 else "ошибка: " + (r.stderr or r.stdout).strip()[-200:]
    except Exception as e:
        state = f"ошибка: {e}"
    _import()                # сначала загрузить, потом снять «ставится» — чтобы между ними не сказать «не установилась»
    INSTALL_STATE = state

def ensure():
    """Пусто — можно читать; иначе текст для модели (библиотека ставится / не поставилась)."""
    global INSTALL_STATE
    if pypdf or _import():
        return ""
    with _lock:
        if INSTALL_STATE == "":
            INSTALL_STATE = "ставится"
            threading.Thread(target=_install, daemon=True).start()
    if INSTALL_STATE == "ставится":
        return ("Библиотека для чтения PDF сейчас устанавливается (около минуты, один раз). "
                "Скажи пользователю попробовать через минуту.")
    return (f"Библиотека pypdf не установилась ({INSTALL_STATE}) — PDF прочитать не могу. "
            f"Починка: pip install {PYPDF} в Python, которым запущен агент.")

def _clean(t):
    t = (t or "").replace("\x00", "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def pages_of(data):
    """Тексты страниц PDF из байтов → (список текстов, ошибка)."""
    err = ensure()
    if err:
        return None, err
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return None, "PDF защищён паролем — прочитать нельзя."
        out = []
        for page in reader.pages:
            try:
                out.append(_clean(page.extract_text()))
            except Exception:
                out.append("")
        return out, ""
    except Exception as e:
        return None, f"Не удалось разобрать PDF: {e}"

def format_pages(pages, name, start=1, end=0, more_hint="start"):
    """Страницы start..end (с 1) частями ≤ READ_CHARS. more_hint — как попросить продолжение."""
    total = len(pages)
    if not total:
        return f"{name}: в PDF нет страниц."
    if not any(pages):
        return (f"{name}: {total} стр., но текста в нём нет — похоже, это скан (картинки страниц). "
                "Прочитать такой PDF не могу: скажи пользователю, что нужен PDF с текстом или распознавание (OCR).")
    start = max(1, min(int(start or 1), total))
    end = min(int(end or 0) or total, total)
    out, size, last = [], 0, start - 1
    for n in range(start, end + 1):
        t = pages[n - 1] or "(на странице нет текста)"
        row = f"--- страница {n} ---\n{t}"
        if size + len(row) > READ_CHARS and out:
            break
        if len(row) > READ_CHARS:  # одна огромная страница — сколько влезет
            row = row[:READ_CHARS] + " …(страница обрезана)"
        out.append(row)
        size += len(row) + 1
        last = n
    head = f"{name}: страниц {total}, показаны {start}–{last}"
    tail = f"\n(дальше: {more_hint}={last + 1})" if last < end else ""
    return head + "\n" + "\n".join(out) + tail

def read_file(path, start=1, end=0):
    p = pathlib.Path(str(path).strip().strip('"'))
    if not p.is_file():
        return f"Файл не найден: {path}"
    try:
        data = p.read_bytes()
    except OSError as e:
        return f"Не прочитать файл: {e}"
    pages, err = pages_of(data)
    if err:
        return err
    return format_pages(pages, p.name, start, end, "doc_read start")

def is_pdf(data, ctype="", url=""):
    return (data or b"")[:5] == b"%PDF-" or "pdf" in (ctype or "").lower() or str(url).lower().split("?")[0].endswith(".pdf")

if __name__ == "__main__":
    print(read_file(sys.argv[1], *(sys.argv[2:4])) if len(sys.argv) > 1 else "python pdfread.py файл.pdf [с] [по]")
