# Документы Word для агента: создать, прочитать, править (.docx — python-docx; .doc и PDF — через установленный Word).
# Модель не трогает формат файла: пишет текст в простой разметке, а правит по номерам блоков из doc_read.
# Разметка (doc_create и вставка):
#   # Заголовок 1   ## Заголовок 2   ### Заголовок 3
#   - пункт списка   1. пункт нумерованного списка
#   | a | b |  — строки таблицы подряд (первая строка — шапка, строка |---| пропускается)
#   **жирный**  *курсив*      [разрыв страницы] — отдельной строкой
#   пустая строка — просто разделитель; каждая непустая строка — отдельный абзац
# .doc: перед чтением/правкой Word делает из него рабочую копию .docx, после правки сохраняет обратно в .doc.
# Перед любой правкой — копия файла в <данные>\doc_backups\.
import datetime, hashlib, json, os, pathlib, re, shutil, subprocess, tempfile
import settings, pdfread

IMPORT_ERROR = ""

def _import_docx():
    global docx, WD_BREAK, Table, Paragraph
    import docx
    from docx.enum.text import WD_BREAK
    from docx.table import Table
    from docx.text.paragraph import Paragraph

try:
    _import_docx()
except ImportError:
    # окно запускает Python с -s (без пользовательской папки библиотек), а зависимости python-docx
    # (typing_extensions, lxml) иногда стоят именно там — подключить её и попробовать ещё раз
    try:
        import site, sys
        user_site = site.getusersitepackages()
        if user_site and user_site not in sys.path and os.path.isdir(user_site):
            sys.path.append(user_site)
        _import_docx()
    except ImportError as e:  # без python-docx инструменты честно скажут, что не работают и почему
        docx = None
        IMPORT_ERROR = str(e)

HERE = pathlib.Path(__file__).resolve().parent
WORK = settings.DATA / "doc_work"          # рабочие копии .doc → .docx
BACKUPS = settings.DATA / "doc_backups"    # копии перед правкой
READ_CHARS = 6000
confirm = lambda q: False                  # подменяет agent (подтверждение пользователя)

INSTALL_STATE = ""  # лайт ставит python-docx в фоне: "ставится" / "ошибка: …" — для понятного ответа

def _need():
    global IMPORT_ERROR
    if docx is None:  # могли доставить после запуска (лайт ставит в фоне) — попробовать ещё раз
        try:
            import importlib
            importlib.invalidate_caches()
            _import_docx()
        except ImportError as e:
            IMPORT_ERROR = str(e)
    if docx is None and INSTALL_STATE == "ставится":
        return "Библиотека для документов Word сейчас устанавливается (около минуты). Скажи пользователю попробовать чуть позже."
    if docx is None:
        return (f"Библиотека python-docx не загрузилась ({IMPORT_ERROR}) — с документами Word работать не могу. "
                "Скажи пользователю эту ошибку; починка: pip install python-docx в Python, которым запущен агент.")
    return ""

# ---------- Word (COM через PowerShell) ----------

def word_convert(src, dst, fmt):
    """Сконвертировать документ установленным Word: fmt — docx, doc или pdf. Вернуть пусто или текст ошибки."""
    job = pathlib.Path(tempfile.gettempdir()) / f"nero_word_{os.getpid()}.json"
    job.write_text(json.dumps({"src": str(src), "dst": str(dst), "format": fmt}, ensure_ascii=False), encoding="utf-8")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "word.ps1"), "-Job", str(job)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "Word не ответил за 3 минуты (может, ждёт ответа в своём окне — проверь Word)."
    finally:
        try:
            job.unlink()
        except OSError:
            pass
    out = (r.stdout or "").strip().splitlines()
    if r.returncode == 0 and out and out[-1] == "OK":
        return ""
    msg = next((l for l in out if l.startswith("ERROR")), "") or (r.stderr or "").strip()[:300] or f"код {r.returncode}"
    if "80040154" in msg or "Word.Application" in msg:
        return "Microsoft Word не найден — .doc и PDF без него не сделать (файлы .docx работают и так)."
    return "Word: " + msg.replace("ERROR: ", "")

def _work_copy(path):
    """Для .doc — рабочая копия .docx (пересоздаётся, если .doc новее). Вернуть (путь docx, ошибка)."""
    p = pathlib.Path(path)
    if p.suffix.lower() == ".docx":
        return p, ""
    if p.suffix.lower() != ".doc":
        return None, "Это не документ Word (.doc / .docx)."
    WORK.mkdir(parents=True, exist_ok=True)
    w = WORK / f"{p.stem}_{hashlib.sha1(str(p.resolve()).lower().encode()).hexdigest()[:8]}.docx"
    if not w.exists() or w.stat().st_mtime < p.stat().st_mtime:
        err = word_convert(p, w, "docx")
        if err:
            return None, err
    return w, ""

def _backup(p):
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.datetime.now():%Y%m%d_%H%M%S}"
    dst, k = BACKUPS / f"{p.stem}_{stamp}{p.suffix}", 1
    while dst.exists():  # несколько правок в одну секунду — каждая своя копия, исходник не затирается
        k += 1
        dst = BACKUPS / f"{p.stem}_{stamp}_{k}{p.suffix}"
    shutil.copy2(p, dst)
    return dst

def _save(d, path, work):
    """Сохранить: .docx — на место; .doc — рабочую копию и обратно в .doc через Word."""
    p = pathlib.Path(path)
    if p.suffix.lower() == ".docx":
        d.save(str(p))
        return ""
    d.save(str(work))
    tmp = p.with_name(p.stem + ".__nero_tmp.doc")
    err = word_convert(work, tmp, "doc")
    if err:
        return err
    os.replace(tmp, p)
    os.utime(work, None)  # рабочая копия не старше .doc — не пересоздавать зря
    return ""

# ---------- Разметка → документ ----------

INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*)")

def _add_runs(par, text, base=None):
    """Текст с **жирным** и *курсивом* — куски (runs) абзаца. base — run, у которого взять шрифт."""
    for part in INLINE.split(text):
        if not part:
            continue
        bold = part.startswith("**") and part.endswith("**") and len(part) > 4
        ital = not bold and part.startswith("*") and part.endswith("*") and len(part) > 2
        r = par.add_run(part[2:-2] if bold else part[1:-1] if ital else part)
        if base is not None:
            r.font.name, r.font.size = base.font.name, base.font.size
            if base.font.color is not None and base.font.color.rgb is not None:
                r.font.color.rgb = base.font.color.rgb
        if bold:
            r.bold = True
        if ital:
            r.italic = True

def _style(d, name):
    try:
        return d.styles[name]
    except KeyError:
        return None

def _blocks_from_markup(d, text):
    """Добавить в конец документа блоки по разметке. Вернуть список новых элементов (для переноса в нужное место)."""
    made = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            if rows:
                cols = max(len(r) for r in rows)
                t = d.add_table(rows=len(rows), cols=cols)
                if _style(d, "Table Grid"):
                    t.style = "Table Grid"
                for ri, r in enumerate(rows):
                    for ci in range(cols):
                        cell = t.cell(ri, ci)
                        cell.paragraphs[0].text = ""
                        _add_runs(cell.paragraphs[0], r[ci] if ci < len(r) else "")
                        if ri == 0:
                            for run in cell.paragraphs[0].runs:
                                run.bold = True
                made.append(t._tbl)
            continue
        if s.lower() in ("[разрыв страницы]", "[page break]"):
            p = d.add_paragraph()
            p.add_run().add_break(WD_BREAK.PAGE)
            made.append(p._p)
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            p = d.add_paragraph(style=_style(d, f"Heading {len(m.group(1))}"))
            _add_runs(p, m.group(2))
        elif re.match(r"^[-*•]\s+", s):
            p = d.add_paragraph(style=_style(d, "List Bullet"))
            _add_runs(p, re.sub(r"^[-*•]\s+", "", s))
        elif re.match(r"^\d+[.)]\s+", s):
            p = d.add_paragraph(style=_style(d, "List Number"))
            _add_runs(p, re.sub(r"^\d+[.)]\s+", "", s))
        else:
            p = d.add_paragraph()
            _add_runs(p, s)
        made.append(p._p)
        i += 1
    return made

# ---------- Блоки документа: абзацы и таблицы по порядку ----------

def _blocks(d):
    return list(d.iter_inner_content())

def _block_text(b):
    if isinstance(b, Table):
        rows = []
        for r in b.rows:
            rows.append(" | ".join(c.text.replace("\n", " / ") for c in r.cells))
        return f"[таблица {len(b.rows)}×{len(b.columns)}]\n" + "\n".join("      " + r for r in rows)
    style = b.style.name if b.style is not None else ""
    low = style.lower()
    tag = ""
    if low.startswith(("heading", "заголовок", "title", "название")):
        tag = "[" + style + "] "
    elif "number" in low or "нумер" in low:
        tag = "1) "
    elif "list" in low or "список" in low:
        tag = "• "
    if not b.text.strip() and b._p.xpath('.//w:br[@w:type="page"]'):
        return "[разрыв страницы]"
    return tag + b.text

# ---------- Инструменты ----------

def doc_create(path, content, overwrite=False, **_):
    """Создать документ .docx (или .doc через Word) из разметки."""
    if _need():
        return _need()
    p = pathlib.Path(str(path).strip().strip('"'))
    if p.suffix.lower() not in (".docx", ".doc"):
        return "Укажи путь с расширением .docx (или .doc)."
    if p.exists() and not overwrite:
        return f"Файл {p} уже есть. Правка — doc_edit (сначала doc_read); перезаписать целиком — overwrite=true."
    d = docx.Document()
    made = _blocks_from_markup(d, content or "")
    if not made:
        return "Пустой текст — нечего записывать."
    if not confirm(f"{'Перезаписать' if p.exists() else 'Создать'} документ {p}: {len(made)} блоков "
                   f"({len(content)} симв.)\n--- начало:\n{content[:500]}"):
        return "Пользователь отклонил."
    p.parent.mkdir(parents=True, exist_ok=True)
    bak = _backup(p) if p.exists() else None
    if p.suffix.lower() == ".docx":
        d.save(str(p))
    else:
        WORK.mkdir(parents=True, exist_ok=True)
        tmp = WORK / f"new_{os.getpid()}.docx"
        d.save(str(tmp))
        err = word_convert(tmp, p, "doc")
        tmp.unlink(missing_ok=True)
        if err:
            return "Не создан: " + err
    return f"Создан документ: {p} (блоков: {len(made)})" + (f". Прежний — в копии {bak}" if bak else "")

def doc_read(path, start=1, end=0, **_):
    """Текст документа по блокам с номерами (абзацы и таблицы). PDF — по страницам (start/end — номера страниц)."""
    if str(path).strip().strip('"').lower().endswith(".pdf"):
        return pdfread.read_file(path, start, end)
    if _need():
        return _need()
    p = pathlib.Path(str(path).strip().strip('"'))
    if not p.is_file():
        return f"Файл не найден: {path}"
    work, err = _work_copy(p)
    if err:
        return err
    d = docx.Document(str(work))
    blocks = _blocks(d)
    start = max(1, int(start or 1))
    end = int(end or 0) or len(blocks)
    out, size, last = [], 0, start - 1
    for n in range(start, min(end, len(blocks)) + 1):
        t = _block_text(blocks[n - 1])
        if not t.strip():
            continue  # пустые абзацы не показываем, номера сохраняются
        row = f"[{n}] {t}"
        if size + len(row) > READ_CHARS and out:
            break
        out.append(row)
        size += len(row) + 1
        last = n
    head = f"{p.name}: блоков {len(blocks)}, показаны {start}–{last}"
    tail = f"\n(дальше: doc_read start={last + 1})" if last < min(end, len(blocks)) else ""
    return head + "\n" + "\n".join(out) + tail if out else head + "\n(пусто)"

def _replace_in_par(par, old, new):
    """Заменить в абзаце, сохраняя оформление: сначала внутри одного куска; если текст разбит по кускам — склеить в первый."""
    n = 0
    for r in par.runs:
        if old in r.text:
            n += r.text.count(old)
            r.text = r.text.replace(old, new)
    if n == 0 and old in par.text:
        full = par.text
        n = full.count(old)
        runs = par.runs
        runs[0].text = full.replace(old, new)
        for r in runs[1:]:
            r.text = ""
    return n

def _all_pars(d):
    for b in _blocks(d):
        if isinstance(b, Table):
            for row in b.rows:
                for c in row.cells:
                    yield from c.paragraphs
        else:
            yield b
    for s in d.sections:  # колонтитулы
        for part in (s.header, s.footer):
            if part is not None and not part.is_linked_to_previous:
                yield from part.paragraphs

def _row_key(s):
    return [c.strip().lower() for c in s.strip().strip("|").split("|")]

def _find_rows(d, old):
    """Строки таблиц, совпадающие с «a | b | c» (без учёта регистра и пробелов по краям ячеек)."""
    want = _row_key(old)
    out = []
    for b in _blocks(d):
        if isinstance(b, Table):
            for row in b.rows:
                if [c.text.strip().lower() for c in row.cells] == want:
                    out.append(row)
    return out

def _set_cell(cell, text):
    """Текст ячейки, оформление первого куска сохраняется."""
    par = cell.paragraphs[0]
    base = par.runs[0] if par.runs else None
    bold = base.bold if base is not None else None
    for extra in cell.paragraphs[1:]:
        extra._p.getparent().remove(extra._p)
    for r in list(par.runs):
        r._r.getparent().remove(r._r)
    _add_runs(par, text, base)
    if bold:
        for r in par.runs:
            r.bold = True

def doc_edit(path, action, n=0, text="", old="", new="", count=1, **_):
    """Правка документа. action: replace (old→new по всему документу), set (блок n — новый текст, оформление абзаца остаётся),
    insert (после блока n вставить разметку text; n=0 — в начало), append (разметку text в конец), delete (блоки n..n+count-1)."""
    if _need():
        return _need()
    p = pathlib.Path(str(path).strip().strip('"'))
    if not p.is_file():
        return f"Файл не найден: {path}"
    work, err = _work_copy(p)
    if err:
        return err
    d = docx.Document(str(work))
    blocks = _blocks(d)
    n, count = int(n or 0), max(1, int(count or 1))
    action = (action or "").lower()
    if action in ("set", "insert", "append") and not (text or "").strip():
        text = new  # модель часто кладёт текст в new
    if action == "replace":
        if not old:
            return "Для replace нужен old — точный текст из doc_read."
        if old == new:
            return "Ничего не изменено: new совпадает с old."
        hits = sum(par.text.count(old) for par in _all_pars(d))
        rows = _find_rows(d, old) if not hits and "|" in old else []
        if not hits and not rows:
            return "Такого текста в документе нет. Посмотри doc_read и скопируй old точно (можно короче — часть фразы)."
        if rows:  # строка таблицы «a | b | c» → ячейки строки
            cells = [c.strip() for c in new.strip().strip("|").split("|")]
            q = f"Строка таблицы в {p.name} ({len(rows)} шт.):\n--- было: {old[:300]}\n+++ станет: {' | '.join(cells)[:300]}"
            def do():
                for row in rows:
                    for ci, cell in enumerate(row.cells):
                        if ci < len(cells) and cell.text.strip() != cells[ci]:
                            _set_cell(cell, cells[ci])
                return len(rows)
        else:
            q = f"Заменить в {p.name} ({hits} раз):\n--- было: {old[:300]}\n+++ станет: {new[:300]}"
            def do():
                return sum(_replace_in_par(par, old, new) for par in _all_pars(d))
    elif action == "set":
        if not 1 <= n <= len(blocks) or isinstance(blocks[n - 1], Table):
            return f"Блок {n} — не абзац (всего блоков {len(blocks)}). Таблицу правь через replace."
        par = blocks[n - 1]
        q = f"Абзац [{n}] в {p.name}:\n--- было: {par.text[:400]}\n+++ станет: {text[:400]}"
        def do():
            base = par.runs[0] if par.runs else None
            for r in list(par.runs):
                r._r.getparent().remove(r._r)
            _add_runs(par, text, base)
            return 1
    elif action in ("insert", "append"):
        if not text.strip():
            return "Нужен text — что вставить (разметка как в doc_create)."
        if action == "insert" and not 0 <= n <= len(blocks):
            return f"Нет блока {n} (всего {len(blocks)})."
        where = "в конец" if action == "append" else ("в начало" if n == 0 else f"после блока [{n}]")
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        target = blocks[n - 1] if action == "insert" and n else None
        if isinstance(target, Table) and lines and all(l.startswith("|") for l in lines):
            # строки «| a | b |» после таблицы — дописать в саму таблицу
            new_rows = [[c.strip() for c in l.strip("|").split("|")] for l in lines
                        if not all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in l.strip("|").split("|") if c.strip())]
            q = f"Добавить в таблицу [{n}] в {p.name} строк: {len(new_rows)}\n" + "\n".join(lines)[:600]
            def do():
                for r in new_rows:
                    cells = target.add_row().cells
                    for ci, cell in enumerate(cells):
                        _set_cell(cell, r[ci] if ci < len(r) else "")
                return len(new_rows)
            if not confirm(q):
                return "Пользователь отклонил правку."
            bak = _backup(p)
            done = do()
            err = _save(d, p, work)
            return f"Не сохранено: {err} (копия: {bak})." if err else f"Готово: {p.name}, строк добавлено {done}. Копия до правки: {bak}"
        q = f"Вставить в {p.name} {where}:\n{text[:600]}"
        def do():
            made = _blocks_from_markup(d, text)
            if action == "insert":
                anchor = blocks[n - 1]._element if n else None
                body = d.element.body
                for el in made:
                    if anchor is None:
                        body.insert(0, el)
                    else:
                        anchor.addnext(el)
                    anchor = el
            return len(made)
    elif action == "delete":
        if not 1 <= n <= len(blocks):
            return f"Нет блока {n} (всего {len(blocks)})."
        gone = blocks[n - 1:n - 1 + count]
        q = f"Удалить из {p.name} блоки [{n}–{n + len(gone) - 1}]:\n" + "\n".join(_block_text(b)[:200] for b in gone)
        def do():
            for b in gone:
                b._element.getparent().remove(b._element)
            return len(gone)
    else:
        return "action: replace, set, insert, append или delete."
    if not confirm(q):
        return "Пользователь отклонил правку."
    bak = _backup(p)
    done = do()
    err = _save(d, p, work)
    if err:
        return f"Не сохранено: {err}. Документ не изменён (копия: {bak})."
    return f"Готово: {p.name}, изменено {done}. Копия до правки: {bak}"

def pdf_to_word(path, out="", **_):
    """PDF → документ Word (.docx) установленным Word: вёрстка, таблицы и шрифты сохраняются (насколько Word их распознаёт).
    Дальше копию правят обычным doc_read / doc_edit."""
    p = pathlib.Path(str(path).strip().strip('"'))
    if not p.is_file():
        return f"Файл не найден: {path}"
    if p.suffix.lower() != ".pdf":
        return "Это не PDF. Документ Word читается doc_read, сохранить как PDF — doc_to_pdf."
    dst = pathlib.Path(str(out).strip().strip('"')) if str(out or "").strip() else p.with_suffix(".docx")
    if dst.suffix.lower() not in (".docx", ".doc"):
        dst = dst.with_suffix(".docx")
    if dst.exists():  # существующий файл не затираем — рядом с номером
        for i in range(2, 100):
            cand = dst.with_name(f"{dst.stem} ({i}){dst.suffix}")
            if not cand.exists():
                dst = cand
                break
    dst.parent.mkdir(parents=True, exist_ok=True)
    err = word_convert(p, dst, dst.suffix.lower().lstrip("."))
    if err:
        return f"Ошибка: {err}"
    return (f"Готово: {dst}\nЭто копия PDF в Word (вёрстка из PDF). Заменить слова, имена, даты — doc_edit action=replace "
            "(old → new по всему документу); посмотреть, что получилось, — doc_read.")

def doc_to_pdf(path, **_):
    """Сохранить документ как PDF рядом (через Word)."""
    p = pathlib.Path(str(path).strip().strip('"'))
    if not p.is_file():
        return f"Файл не найден: {path}"
    dst = p.with_suffix(".pdf")
    err = word_convert(p, dst, "pdf")
    return f"Ошибка: {err}" if err else f"PDF сохранён: {dst}"
