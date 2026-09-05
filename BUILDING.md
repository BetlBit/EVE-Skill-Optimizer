# Сборка EVE Skill Optimizer 0.9.10

## Локальная сборка на Windows

Требования:

- Windows x64;
- Python 3.11;
- Inno Setup 6;
- интернет при первом `pip install`.

### 1. Окружение

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 2. Полная сборка

```powershell
.\build_release.ps1
```

Скрипт:

1. читает версию из `app/version.py`;
2. запускает тесты;
3. устанавливает PyInstaller в `.venv`, если его нет;
4. генерирует Windows version resource;
5. собирает onedir EXE;
6. запускает frozen smoke test;
7. создаёт portable ZIP;
8. создаёт чистый source ZIP;
9. собирает installer через Inno Setup;
10. создаёт `SHA256SUMS.txt`.

### 3. Результат

```text
release/
  EVE-Skill-Optimizer-Setup-0.9.10.exe
  EVE-Skill-Optimizer-Portable-0.9.10.zip
  EVE-Skill-Optimizer-Source-0.9.10.zip
  SHA256SUMS.txt
```

## Быстрый запуск

Можно дважды открыть:

```text
BUILD_WINDOWS.cmd
```

Он создаст `.venv`, если её нет, установит зависимости и вызовет `build_release.ps1`.

## GitHub Actions

Workflow: `.github/workflows/windows-release.yml`.

### Просто получить build artifact

GitHub → Actions → `Build Windows Release` → `Run workflow`.

### Опубликовать GitHub Release

```powershell
git tag v0.9.10
git push origin v0.9.10
```

Workflow прикрепит содержимое `release/` к GitHub Release для этого тега.

## Что нельзя публиковать

Не добавляй в репозиторий:

- `.env`;
- `.venv/`;
- `data/`;
- EVEMon Settings backup / `settings.xml.bak`;
- runtime snapshots;
- `dist/`, `release/`, `build/pyinstaller/`.
