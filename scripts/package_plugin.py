"""Build a clean, copyable plugin ZIP; never installs into STE Slicer."""
import argparse
import json
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "plugin", "AIAssistant")
EXTENSIONS = (".py", ".json", ".html", ".css", ".js", ".svg", ".png", ".jpg")
REQUIRED = ("plugin.json", "__init__.py", "AIAssistant.py", "server.py",
            "agent/service.py", "slicer/adapter.py", "web/index.html")


def package_plugin(source, output):
    source, output = os.path.abspath(source), os.path.abspath(output)
    if os.path.islink(source):
        raise ValueError("Plugin directory must not be a link")
    for name in REQUIRED:
        if not os.path.isfile(os.path.join(source, *name.split("/"))):
            raise ValueError("Incomplete plugin: " + name)
    with open(os.path.join(source, "plugin.json"), encoding="utf-8") as stream:
        metadata = json.load(stream)
    if metadata.get("api") != 5:
        raise ValueError("Expected plugin api=5")
    if os.path.commonpath([os.path.normcase(source), os.path.normcase(output)]) == os.path.normcase(source):
        raise ValueError("Archive must be outside the plugin directory")
    files = []
    for directory, subdirs, names in os.walk(source, followlinks=False):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith(".") and d != "__pycache__")
        if any(os.path.islink(os.path.join(directory, d)) for d in subdirs):
            raise ValueError("Plugin contains a directory link")
        for name in sorted(names):
            if name.startswith(".") or not name.lower().endswith(EXTENSIONS):
                continue
            path = os.path.join(directory, name)
            if os.path.islink(path):
                raise ValueError("Plugin contains a file link")
            files.append((path, "AIAssistant/" + os.path.relpath(path, source).replace(os.sep, "/")))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    # Exclusive creation avoids replacing an archive the user may already be using.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                for path, name in files:
                    archive.write(path, name)
                archive.writestr("INSTALL.txt", (
                    "AI Assistant " + str(metadata.get("version", "")) + "\n\n"
                    "1. В STE Slicer: Справка -> Показать папку конфигурации.\n"
                    "2. Сохраните проект и закройте STE Slicer.\n"
                    "3. Распакуйте этот ZIP в отдельную обычную папку. Не копируйте из окна архива.\n"
                    "4. Сохраните резервную копию старого AIAssistant вне каталога plugins.\n"
                    "5. Замените целиком только plugins/AIAssistant папкой AIAssistant из архива.\n"
                    "6. Проверьте: plugins/AIAssistant/plugin.json. Вложенной AIAssistant/AIAssistant быть не должно.\n"
                    "7. Запустите STE Slicer -> AI Assistant -> Open chat.\n\n"
                    "Если Windows отказывает в доступе, не удаляйте остальные настройки.\n"
                    "Запишите текст ошибки и проверьте, что STE закрыт и выбрана пользовательская папка конфигурации.\n"
                    "Путь Program Files и каталог всего репозитория для этой операции не нужны.\n"
                ).encode("utf-8-sig"))
    except Exception:
        os.remove(output)  # Only the file exclusively created above.
        raise
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New ZIP path (existing files are not overwritten)")
    args = parser.parse_args()
    count = package_plugin(SOURCE, args.output)
    print("Plugin archive created: %d source files; no installation performed" % count)


if __name__ == "__main__":
    main()
