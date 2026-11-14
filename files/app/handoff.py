# Передача задачи в новую сессию и учёт времени и токенов (общий модуль полного агента и лайта).
# Сессия — кусок задачи, пока история влезает в контекст модели. Когда занято HANDOFF_AT (по умолчанию 70%) контекста,
# модель пишет записку «что сделано / что осталось / следующий шаг», история заменяется этой запиской — и работа
# продолжается в новой сессии с почти пустым контекстом. Время и токены считаются на сессию и на всю задачу.
import time

import settings

DEFAULT_AT = 0.7   # доля контекста, при которой задача передаётся в следующую сессию (config.json: handoff_at, 0.6–0.8)
MAX_SESSIONS = 6
MIN_GROWTH = 0.15  # сессия должна набрать хотя бы столько контекста — иначе записка ничего не освободит (передавать незачем)   # больше передач на одно сообщение — вероятно, модель ходит по кругу: остановиться и спросить

def threshold():
    try:
        v = float(settings.get("handoff_at", DEFAULT_AT))
    except (TypeError, ValueError):
        v = DEFAULT_AT
    return max(0.4, min(0.9, v))

def tokens_text(n):
    n = int(n or 0)
    return f"{n / 1000:.1f} тыс." if n >= 1000 else str(n)

def time_text(sec):
    sec = int(sec or 0)
    if sec < 60:
        return f"{sec} с"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} мин {s:02d} с"
    h, m = divmod(m, 60)
    return f"{h} ч {m:02d} мин"

class Meter:
    """Счётчик одной задачи (одного сообщения пользователя): сессии, время, токены."""
    def __init__(self):
        self.started = time.time()
        self.sessions = []        # закрытые сессии: {n, sec, prompt, out, calls, peak}
        self._new()

    def _new(self):
        self.s_started = time.time()
        self.prompt = self.out = self.calls = 0
        self.peak = 0.0           # наибольшая занятость контекста в сессии (доля)
        self.base = getattr(self, "base", 0.0)  # занятость в начале сессии (system, инструменты, записка)
        self.usage = 0.0          # занятость после последнего ответа

    def note(self, info):
        """info — из chat(): prompt (токены запроса), out (токены ответа), used (токены в контексте), num_ctx."""
        if not info:
            return
        self.calls += 1
        self.prompt += int(info.get("prompt") or 0)
        self.out += int(info.get("out") or 0)
        ctx = info.get("num_ctx") or 0
        self.usage = (info.get("used") or 0) / ctx if ctx else 0.0
        self.peak = max(self.peak, self.usage)

    def full(self):
        return self.usage >= threshold()

    @property
    def number(self):
        return len(self.sessions) + 1

    def close(self):
        """Закрыть текущую сессию, вернуть её итог и начать новую."""
        s = {"n": self.number, "sec": time.time() - self.s_started, "prompt": self.prompt, "out": self.out,
             "calls": self.calls, "peak": self.peak}
        self.sessions.append(s)
        self._new()
        return s

    def session_text(self, s):
        return (f"Сессия {s['n']}: {time_text(s['sec'])}, токены — запрос {tokens_text(s['prompt'])}, "
                f"ответ {tokens_text(s['out'])}, контекст до {round(s['peak'] * 100)}%")

    def task_text(self):
        """Итог задачи (вызывать в конце; текущая сессия тоже считается)."""
        prompt = sum(s["prompt"] for s in self.sessions) + self.prompt
        out = sum(s["out"] for s in self.sessions) + self.out
        n = self.number
        parts = [f"⏱ {time_text(time.time() - self.started)}", f"токены: запрос {tokens_text(prompt)}, ответ {tokens_text(out)}",
                 f"контекст {round(self.usage * 100)}%"]
        if n > 1:
            parts.insert(1, f"сессий {n}")
        return " · ".join(parts)

    def stats(self):
        prompt = sum(s["prompt"] for s in self.sessions) + self.prompt
        out = sum(s["out"] for s in self.sessions) + self.out
        return {"sec": round(time.time() - self.started, 1), "prompt": prompt, "out": out, "sessions": self.number,
                "usage": round(self.usage, 3)}

NOTE_REQUEST = (
    "[Служебное: контекст модели почти заполнен ({pct}%). Задача переходит в новую сессию, где ты увидишь ТОЛЬКО "
    "эту записку и исходную просьбу — вся остальная история исчезнет. НЕ вызывай инструменты. Напиши записку для себя:\n"
    "ЗАДАЧА: что просил пользователь (своими словами, со всеми важными условиями).\n"
    "СДЕЛАНО: что уже выполнено и проверено — конкретно: файлы, функции, пути, найденные факты, числа.\n"
    "ВАЖНО: что выяснилось по ходу (причины ошибок, что не сработало и почему, решения пользователя).\n"
    "ОСТАЛОСЬ: что ещё сделать, по шагам.\n"
    "СЛЕДУЮЩИЙ ШАГ: с какого действия начать новую сессию.\n"
    "Пиши коротко, но так, чтобы продолжить без истории. Не выдумывай: чего не знаешь — так и пиши.]")

def note_request(meter):
    return {"role": "user", "content": NOTE_REQUEST.format(pct=round(meter.usage * 100))}

def task_text_of(messages):
    """Исходная просьба текущей задачи: последнее сообщение пользователя, не служебное."""
    for m in reversed(messages):
        c = m.get("content") or ""
        if m.get("role") != "user" or c.startswith("[Служебное"):
            continue
        if c.startswith("[Продолжение задачи"):  # уже передавали — просьба внутри прошлой передачи
            a = c.find(TASK_HEAD)
            b = c.find(NOTE_HEAD, a)
            if a >= 0:
                return c[a + len(TASK_HEAD):b if b > 0 else None].strip()
            continue
        return c
    return ""

TASK_HEAD = "Исходная просьба пользователя:\n"
NOTE_HEAD = "\n\nЗаписка из прошлой сессии:\n"

def rebuild(messages, note, task, session_no):
    """Новая сессия: system + одна реплика — исходная просьба и записка. Список меняется на месте."""
    system = messages[0] if messages and messages[0].get("role") == "system" else None
    task = task if len(task) <= 6000 else task[:6000] + " …(просьба сокращена)"
    text = (f"[Продолжение задачи — сессия {session_no}. Прошлая сессия закончилась: контекст заполнился. "
            "История заменена твоей запиской — продолжай с «СЛЕДУЮЩИЙ ШАГ», не начинай заново и не повторяй сделанное. "
            "Файлы и результаты, о которых записка, перечитай инструментами, если нужны подробности.]\n\n"
            f"{TASK_HEAD}{task}{NOTE_HEAD}{note}")
    messages[:] = ([system] if system else []) + [{"role": "user", "content": text}]
    return text

def _fallback_note(messages):
    """Модель не написала записку — собрать хоть что-то: последние ответы и вызовы инструментов."""
    lines = []
    for m in messages[-40:]:
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            lines.append("Ответ: " + m["content"].strip()[:400])
        for c in m.get("tool_calls") or []:
            lines.append(f"Вызов: {c['function']['name']}")
    return "(записку написать не удалось — последние шаги)\n" + "\n".join(lines[-15:])

def check(messages, start, fill, ask, meter):
    """Перед очередным шагом: контекст занят ≥ порога — передать задачу в новую сессию.
    start — индекс сообщения пользователя, с которого началась задача; fill(messages) — занятая доля контекста;
    ask(messages) — ответ модели текстом, без инструментов. Возвращает (событие или None, новый start):
    {"dropped": True} — хватило убрать старые реплики беседы; {"limit": True} — сессий слишком много;
    {"session": итог закрытой сессии, "note": записка}."""
    thr = threshold()
    used = fill(messages)
    if used < thr:
        return None, start
    if start > 1:  # сначала — старые реплики беседы до этой задачи (так и так выпали бы из контекста)
        del messages[1:start]
        start = 1
        used = fill(messages)
        if used < thr:
            return {"dropped": True}, start
    if used - meter.base < MIN_GROWTH:  # почти всё занято с самого начала сессии — новая сессия не поможет
        return None, start
    if meter.number >= MAX_SESSIONS:
        return {"limit": True}, start
    meter.usage = used
    meter.peak = max(meter.peak, used)
    try:
        note = (ask(messages + [note_request(meter)]) or "").strip()
    except Exception:
        note = ""
    if len(note) < 40:
        note = _fallback_note(messages)
    task = task_text_of(messages)
    s = meter.close()
    rebuild(messages, note, task, meter.number)
    meter.usage = meter.base = fill(messages)
    return {"session": s, "note": note}, 1
