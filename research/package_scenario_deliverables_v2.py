"""Package verified results and reproducible sources without rerunning any scores."""
import ast
import csv
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT.parent / 'artifacts/BR_GOLD_Six_Strategies_V1'


def package():
    files = {}

    def add(path, name=None):
        if not path.is_file():
            raise FileNotFoundError(path)
        files[name or str(path.relative_to(ROOT))] = path.read_bytes()

    modules = [
        'scenario_features_v2', 'scenario_engine_v2', 'run_scenarios_v2',
        'correct_s2_session_v21', 'finalize_scenario_study_v2',
        'register_scenario_study_v2', 'restore_scenario_archives_v2',
        'render_scenario_report_v2', 'export_passports_ru_v2',
        'export_completion_addendum_v2', 'verify_position_sizing_v2',
        'test_scenario_study_v2', 'test_position_sizing_v2',
        'package_scenario_deliverables_v2', 'verify_agent_replay',
        'verify_history_archives', 'verify_timing_evidence_v2',
    ]
    seen = set()
    while modules:
        module = modules.pop()
        if module in seen:
            continue
        seen.add(module)
        path = ROOT / 'research' / (module + '.py')
        add(path)
        for node in ast.walk(ast.parse(path.read_text())):
            imports = ([node.module] if isinstance(node, ast.ImportFrom) else
                       [n.name for n in node.names] if isinstance(node, ast.Import) else [])
            for imported in imports:
                if imported and (ROOT / 'research' / (imported + '.py')).exists():
                    modules.append(imported)

    for directory in ('research/scenario-study-v2', 'research/six-strategy-v1', 'research/data-audit'):
        for path in sorted((ROOT / directory).rglob('*')):
            if path.is_file() and path.suffix in {'.json', '.jsonl', '.txt', '.csv', '.py'}:
                add(path)
    for name in ['research/PROJECT_SPEC_RU.txt', 'research/job-state.json', 'paper_engine.py', 'paper_week.py']:
        add(ROOT / name)
    for name in ['algopack-h1.yml', 'algopack-control.yml', 'algopack-telegram.yml',
                 'algopack-research-data.yml', 'algopack-research-reproduce-scenarios.yml']:
        add(ROOT / '.github/workflows' / name)
    artifacts = ['FINAL_REPORT_RU.pdf', 'FINAL_REPORT_RU.txt', 'COMPARISON_COMPLETE_RU.csv',
                 'COMPARISON_S0_S5.csv', 'VIRTUAL_SCENARIO_TRADES.csv', 'ALL_192_SCENARIOS.csv',
                 'SCENARIO_EQUITY_R.png', 'STRATEGY_PASSPORTS_RU.txt', 'ONLINE_TEST_REQUIREMENTS_RU.txt',
                 'LOT_VALIDATION_RU.txt', 'REPRODUCE_RU.txt', 'DATA_SUFFICIENCY_RU.txt',
                 'data-register-v2.json', 'data-register-v2.csv', 'timing-evidence.json']
    artifacts += [f'S{i}_PASSPORT.txt' for i in range(6)]
    for name in artifacts:
        add(DEST / name, 'deliverables/' + name)
    files['requirements-scenario.txt'] = b'numpy==2.3.5\npandas==2.2.3\n'
    files['README_RU.txt'] = '''BR/GOLD — завершённое сценарное сравнение S0–S5

Результат: ни один вариант не прошёл заранее записанный фильтр устойчивости.
Это ценовая ретроспектива с раскрытыми допущениями, не точное финансовое
воспроизведение действующего агента. Результаты в R не равны рублям или
проценту капитала. Денежные результаты/ГО остаются BLOCKED_DATA.

Начните с deliverables/FINAL_REPORT_RU.pdf и COMPARISON_COMPLETE_RU.csv.
Журнал всех 1844 принятых сделок основного сценария — VIRTUAL_SCENARIO_TRADES.csv.
Паспорта, ограничения данных и требования к будущему онлайн-тесту приложены.
Новый тест не начат: подходящего кандидата нет. Рабочий бот и старый тест
не изменены. Этот архив сам по себе ничего не устанавливает и не запускает.

Учёт попыток: 192 итоговых случая, 32 старых S2 сохранены как superseded,
224 фактических случая с результатами, 208 хронологических обучений S3.
Общий прежний перебор неизвестен. История 2023–2026 уже изучалась.
Поправка S2 была зарегистрирована отдельно до пересчёта только её 32 случаев.
Параметрическая оптимизация после сравнения не выполнялась.

Воспроизведение: Python 3.12; установите requirements-scenario.txt.
Из корня распакованного архива:

PYTHONPATH=research python -m unittest research/test_scenario_study_v2.py research/test_position_sizing_v2.py -v
PYTHONPATH=research python -c "from finalize_scenario_study_v2 import verify; print(verify()[-1])"

Эти команды проверяют сохранённый результат без новых торговых расчётов.
Для полного независимого расчёта см. deliverables/REPRODUCE_RU.txt и ручной
workflow .github/workflows/algopack-research-reproduce-scenarios.yml.
Не регистрируйте повторно использованные trial_id. Исходный run001 привязан
к коммиту e1cdf71c39aea4be489d375914a7581d2040751a и сохранённому точному
research/scenario-study-v2/original-run001-engine.py. Текущий engine также
поддерживает отдельно зарегистрированную поправку S2 v2.1.

Крупные рыночные ZIP и локальный pickle-кеш в этот малый пакет не включены.
Идентификаторы четырёх свечных ZIP, SHA256 и безопасная процедура восстановления
даны в research/restore_scenario_archives_v2.py. Получение только из GitHub
архивов, без публичного ISS, Telegram и брокера. Срок хранения артефактов GitHub
ограничен; сохранённые локальные проверенные ZIP можно использовать повторно.

SHA256 каждого файла указан в MANIFEST_SHA256.json. Пакет содержит итоговые
и исходные результаты, журналы попыток и контрольные тесты. Эти тесты проверяют
причинность/исполнение и формулу количества, но не доказывают историческую
денежную доступность неизвестных входов модели.
'''.encode()
    manifest = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}
    files['MANIFEST_SHA256.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    target = DEST / 'BR_GOLD_S0_S5_REPRODUCIBLE.zip'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=7) as archive:
        for name, raw in sorted(files.items()):
            archive.writestr(name, raw)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for name, sha in manifest.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == sha
    receipt = {'file': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
               'files': len(files), 'bytes': target.stat().st_size, 'all_members_verified': True,
               'final_results_sha256': hashlib.sha256((ROOT / 'research/scenario-study-v2/final-results.json').read_bytes()).hexdigest()}
    (DEST / 'PACKAGE_SHA256.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    package()
