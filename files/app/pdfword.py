# PDF → Word точным конвертером pdf2docx (общий модуль полного агента и лайта).
# pdf2docx тянет PyMuPDF, OpenCV, numpy — чтобы не задеть ComfyUI и сам агент, библиотеки ставятся в ОТДЕЛЬНУЮ папку
# (<данные>\pylibs\pdf2docx) и конвертация идёт ОТДЕЛЬНЫМ процессом, который видит только эту папку.
# Ставится сам при первом использовании (1–3 минуты, ~150 МБ). Не вышло — docs.pdf_to_word делает копию через Word.
import json, os, pathlib, shutil, subprocess, sys, tempfile, threading
import settings

LIB = pathlib.Path(settings.DATA) / "pylibs" / "pdf2docx"
PKGS = ["pdf2docx==0.5.13", "PyMuPDF==1.28.2"]
MARK = LIB / ".ok"
STATE = ""            # "" / "ставится" / "ошибка: …"
_lock = threading.Lock()
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

def ready():
    return MARK.is_file()

def _install():
    global STATE
    tmp = LIB.with_name(LIB.name + ".tmp")
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        r = subprocess.run([sys.executable, "-s", "-m", "pip", "install", "--no-warn-script-location", "-q",
                            "--ignore-installed", "--target", str(tmp), *PKGS],
                           capture_output=True, text=True, timeout=1200, creationflags=NOWIN)
        if r.returncode != 0:
            STATE = "ошибка: " + (r.stderr or r.stdout).strip()[-300:]
            return
        shutil.rmtree(LIB, ignore_errors=True)
        tmp.rename(LIB)
        MARK.write_text("ok", encoding="utf-8")
        STATE = ""
    except Exception as e:
        STATE = f"ошибка: {e}"

def ensure():
    """Пусто — можно конвертировать; иначе почему нет (ставится / ошибка). Установку запускает сам."""
    global STATE
    if ready():
        return ""
    with _lock:
        if STATE == "":
            STATE = "ставится"
            threading.Thread(target=_install, daemon=True).start()
    return STATE

SCRIPT = r'''
import json, logging, sys
sys.path.insert(0, sys.argv[1])
logging.disable(logging.WARNING)
from pdf2docx import Converter
job = json.loads(open(sys.argv[2], encoding="utf-8").read())
cv = Converter(job["src"])
try:
    cv.convert(job["dst"])
finally:
    cv.close()
print("OK")
'''

def convert(src, dst):
    """PDF src → .docx dst. Вернуть пусто или текст ошибки."""
    if not ready():
        return "pdf2docx не установлен"
    job = pathlib.Path(tempfile.gettempdir()) / f"nero_pdf2docx_{os.getpid()}.json"
    script = pathlib.Path(tempfile.gettempdir()) / f"nero_pdf2docx_{os.getpid()}.py"
    job.write_text(json.dumps({"src": str(src), "dst": str(dst)}, ensure_ascii=False), encoding="utf-8")
    script.write_text(SCRIPT, encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "-s", "-I", str(script), str(LIB), str(job)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600, creationflags=NOWIN)
    except subprocess.TimeoutExpired:
        return "конвертация дольше 10 минут — прервана"
    finally:
        for f in (job, script):
            try:
                f.unlink()
            except OSError:
                pass
    if r.returncode == 0 and (r.stdout or "").strip().endswith("OK") and pathlib.Path(dst).is_file():
        return ""
    return ((r.stderr or r.stdout or "").strip().splitlines() or [f"код {r.returncode}"])[-1][:300]
