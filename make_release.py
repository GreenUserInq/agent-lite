# Выпуск новой версии лайт-агента:
#   python make_release.py 1.2.0 "что нового (коротко, по-русски)"
# 1) ставит VERSION в files\app\agent_lite.py; 2) пишет update.json (версия, что нового, файлы с SHA-256);
# 3) пересобирает архив установщика E:\nero\lite_dist\AgentInstaller.zip.
# Потом: git add -A && git commit -m "v1.2.0" && git push — у установленных агентов кнопка «Обновления» увидит новую версию.
import hashlib, json, pathlib, re, sys, zipfile

KIT = pathlib.Path(__file__).parent
DIST = KIT.parent / "lite_dist" / "AgentInstaller.zip"
SKIP_DIRS = {"pylibs", "__pycache__", ".git"}
SKIP_FILES = {"config.json"}

def install_path(rel):
    """Путь файла в репозитории → путь в установленной папке агента."""
    if rel.startswith("files/"):
        return rel[len("files/"):]
    if rel == "README.txt":
        return "README.txt"
    return None

def main():
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit('Запуск: python make_release.py 1.2.0 "что нового"')
    version, notes = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    old = json.loads((KIT / "update.json").read_text(encoding="utf-8")) if (KIT / "update.json").exists() else {}
    if old.get("version") and tuple(map(int, version.split("."))) <= tuple(map(int, old["version"].split("."))):
        sys.exit(f"Версия {version} не новее уже выпущенной {old['version']} — агенты её не увидят.")

    core = KIT / "files" / "app" / "agent_lite.py"
    text = core.read_text(encoding="utf-8")
    text, n = re.subn(r'^VERSION = "[^"]*"', f'VERSION = "{version}"', text, count=1, flags=re.M)
    if n != 1:
        sys.exit("Не нашёл строку VERSION в agent_lite.py")
    core.write_bytes(text.encode("utf-8"))

    files = []
    for f in sorted(KIT.rglob("*")):
        if not f.is_file() or SKIP_DIRS & set(f.relative_to(KIT).parts) or f.name in SKIP_FILES:
            continue
        rel = f.relative_to(KIT).as_posix()
        dst = install_path(rel)
        if dst:
            files.append({"path": dst, "repo": rel, "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "size": f.stat().st_size})
    (KIT / "update.json").write_text(json.dumps({"version": version, "notes": notes, "files": files}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    DIST.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(DIST, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(KIT.rglob("*")):
            parts = set(f.relative_to(KIT).parts)
            if f.is_file() and not (SKIP_DIRS & parts) and f.name not in SKIP_FILES | {"make_release.py", "update.json", ".gitignore", "README.md"}:
                z.write(f, "AgentInstaller/" + f.relative_to(KIT).as_posix())
    print(f"Версия {version}: update.json ({len(files)} файлов), архив {DIST} ({DIST.stat().st_size // 1024} КБ).")
    print(f'Дальше: cd {KIT} && git add -A && git commit -m "v{version}" && git push')

if __name__ == "__main__":
    main()
