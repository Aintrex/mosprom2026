# -*- coding: utf-8 -*-
"""
Скрипт автоматического прогона всех 7 тестовых моделей хакатона из папки «Примеры деталей»
через плагин AIAssistant (MCP-сервер + LLM-агент) и формирования готовой структуры сдачи:
/Users/artem/Downloads/3D5D_печать/submission/
├── plugin/              # исходный код плагина AIAssistant + README.md + ANALYTICS.md
└── results/
    └── <model_name>/
        ├── project.3mf  # настроенный проект, полученный через чатбот
        ├── result.gcode # G-code — результат слайсинга
        └── chat_log.md  # полная расшифровка диалога и вызовов MCP-инструментов
А также упаковывает всё в архив `/Users/artem/Downloads/3D5D_печать/stereotech_team_submission.zip`.
"""

import base64
import json
import os
import shutil
import zipfile

from AIAssistant import AIAssistant, extract_docx_text
from agent.knowledge_base import PRESET_CASES


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plugin_src = os.path.dirname(os.path.abspath(__file__))
    submission_dir = os.path.join(base_dir, "submission")
    results_dir = os.path.join(submission_dir, "results")
    plugin_dst = os.path.join(submission_dir, "plugin")

    if os.path.isdir(submission_dir):
        shutil.rmtree(submission_dir)
    os.makedirs(results_dir)

    # Копируем чистый исходный код плагина в submission/plugin/
    shutil.copytree(
        plugin_src,
        plugin_dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "uploads", "exports", ".DS_Store")
    )

    assistant = AIAssistant()
    examples_dir = assistant.config.get().get("examples_dir", "")
    print("Используется папка примеров: %s" % examples_dir)

    summary_rows = []

    for case in PRESET_CASES:
        assistant.agent.clear_history()
        case_folder = os.path.join(examples_dir, case["folder"])
        stl_path = os.path.join(case_folder, case["stl"])
        jpg_path = os.path.join(case_folder, case["jpg"])
        docx_path = os.path.join(case_folder, case["docx"])

        print("\n------------------------------------------------------------")
        print("Обработка кейса: %s" % case["title"])
        tz_text = extract_docx_text(docx_path)
        images = []
        if os.path.isfile(jpg_path):
            with open(jpg_path, "rb") as jf:
                images.append("data:image/jpeg;base64," + base64.b64encode(jf.read()).decode("ascii"))

        prompt = (
            "Задача печати по ТЗ (%s):\n\n%s\n\n"
            "Загрузи модель %s, проанализируй геометрию, выбери режим печати (3D classic / 5D Spiral cylindrical / 5D cylindrical_full), "
            "материал и профиль, настрой параметры слайсера и запусти слайсинг."
            % (case["title"], tz_text or case["summary"], case["stl"])
        )

        res = assistant.agent.process_user_message(
            user_text=prompt,
            image_data_urls=images,
            model_path=stl_path
        )

        out_model_dir = os.path.join(results_dir, case["folder"])
        os.makedirs(out_model_dir)
        p_3mf = os.path.join(out_model_dir, "project.3mf")
        p_gcode = os.path.join(out_model_dir, "result.gcode")
        p_chat = os.path.join(out_model_dir, "chat_transcript.md")

        r_3mf = assistant.bridge.save_project_3mf(p_3mf)
        r_gcode = assistant.bridge.export_gcode(p_gcode)

        state = res["state"]
        chat_md = [
            "# Протокол работы ИИ-ассистента STE Slicer: %s" % case["title"],
            "",
            "## 1. Запрос пользователя (ТЗ + 3D-модель + Фото)",
            "- **Файл модели**: `%s`" % case["stl"],
            "- **Фото детали**: `%s`" % case["jpg"],
            "",
            "## 2. Выполненные вызовы MCP-инструментов (`tools/call`)",
            "```json",
            json.dumps(res["tool_calls"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## 3. Ответ и инженерное обоснование ИИ-ассистента",
            res["reply"],
            ""
        ]
        with open(p_chat, "wt", encoding="utf-8") as cf:
            cf.write("\n".join(chat_md))

        print(" -> Режим: %s | Материал: %s" % (state["printing_mode"], state["active_material"]))
        print(" -> Время: %s | Расход: %s г" % (
            state["slicing_state"]["print_time_formatted"],
            state["slicing_state"]["filament_weight_g"]
        ))
        print(" -> Сохранено: %s (%d байт), %s (%d байт)" % (
            p_3mf, r_3mf["size_bytes"], p_gcode, r_gcode["size_bytes"]
        ))

        summary_rows.append({
            "case": case["title"],
            "mode": state["printing_mode"],
            "material": state["active_material"],
            "layer_height": state["settings"]["layer_height"],
            "walls": state["settings"]["wall_line_count"],
            "infill": state["settings"]["infill_sparse_density"],
            "supports": state["settings"]["support_enable"],
            "time": state["slicing_state"]["print_time_formatted"],
            "weight_g": state["slicing_state"]["filament_weight_g"]
        })

    # Упаковываем в итоговый ZIP-архив по формату хакатона
    zip_path = os.path.join(base_dir, "stereotech_team_submission.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(submission_dir):
            for file in files:
                if file == ".DS_Store":
                    continue
                abs_f = os.path.join(root, file)
                rel_f = os.path.relpath(abs_f, submission_dir)
                zf.write(abs_f, rel_f)

    print("\n============================================================")
    print("Готово! Все 7 кейсов обработаны и сохранены в:")
    print("  Папка: %s" % submission_dir)
    print("  Архив: %s (%d байт)" % (zip_path, os.path.getsize(zip_path)))
    print("============================================================")


if __name__ == "__main__":
    main()
