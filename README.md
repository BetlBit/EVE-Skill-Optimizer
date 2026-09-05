# EVE Skill Optimizer 0.9.10

Локальное приложение для анализа и оптимизации планов прокачки EVE Online.

## Возможности

- импорт персонажа из локального EVEMon Settings backup;
- несколько целей обучения с приоритетами;
- автоматическое раскрытие prerequisites через EVE SDE;
- оптимизированная очередь навыков с экспортом обратно в буфер EVE;
- расчёт времени с атрибутами, имплантами, ремапами и временными attribute boosters;
- NES cerebral accelerators (+2/+4/+6/+8/+10/+12) с Biology и BY-805/BY-810;
- Jita 4-4 economics, PLEX и Skill Injectors;
- ручные и автоматические комбинации NES + Large Skill Injectors;
- русскоязычный локальный web UI;
- Windows portable build и installer.

> Функция «фит корабля → план навыков III/IV/V (Perfect)» запланирована отдельно и в 0.9.10 ещё не реализована.

## Безопасность

EVEMon Settings backup может содержать ESI credentials/refresh tokens. Не публикуй этот файл и не добавляй его в Git.
Приложение читает backup локально и сохраняет только нормализованный snapshot персонажа. `.env`, `data/`, runtime snapshots и EVEMon backups исключены из публичного исходника.

## Быстрый запуск из исходников

Требуется Python 3.11+.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m pytest -q
python -m uvicorn app.main:app --port 8000
```

Открыть: `http://127.0.0.1:8000/`

## Сборка Windows release

На Windows с Python 3.11 и Inno Setup 6:

```powershell
.\build_release.ps1
```

Или дважды запусти `BUILD_WINDOWS.cmd`.

Результат в `release\`:

- `EVE-Skill-Optimizer-Setup-0.9.10.exe` — установщик;
- `EVE-Skill-Optimizer-Portable-0.9.10.zip` — portable;
- `EVE-Skill-Optimizer-Source-0.9.10.zip` — чистый исходник;
- `SHA256SUMS.txt` — SHA-256.

Подробно: [BUILDING.md](BUILDING.md).

## GitHub Actions

В репозитории есть workflow `.github/workflows/windows-release.yml`.

- `workflow_dispatch` собирает артефакты вручную в Actions;
- push тега `v0.9.10` собирает Windows release и прикрепляет installer/portable/source/checksums к GitHub Release.

Пример:

```powershell
git tag v0.9.10
git push origin v0.9.10
```

GitHub также автоматически добавит свои стандартные `Source code (zip)` / `Source code (tar.gz)`.

## Данные пользователя

У установленной/frozen версии mutable data хранятся вне каталога программы:

`%LOCALAPPDATA%\EveSkillOptimizer\`

Приложение слушает только `127.0.0.1`.

## Лицензия

Лицензия в этот пакет намеренно не добавлена. Перед публикацией репозитория выбери подходящую лицензию и добавь `LICENSE`, если хочешь разрешить стороннее использование/модификацию/распространение.
