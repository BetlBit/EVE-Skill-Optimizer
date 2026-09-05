from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_root_returns_html_200():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "EVE Skill Optimizer" in response.text


def test_static_js_served():
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "runEconomics" in response.text


def test_static_css_served():
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    assert "--bg" in response.text


def test_status_endpoint():
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["version"] == "0.9.10"


def test_docs_still_available():
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_root_does_not_render_secret_fields():
    response = client.get("/")
    lowered = response.text.lower()
    assert "refresh_token" not in lowered
    assert "clientsecret" not in lowered
    assert "access_token" not in lowered


def test_template_contains_required_sections_and_tabs():
    response = client.get("/")
    for marker in (
        "tab-character",
        "tab-plans",
        "tab-schedule",
        "tab-time",
        "tab-economics",
        "tab-summary",
        "tab-raw",
        "Итоговая рекомендация",
        "Рекомендованная очередь",
        "Экспорт",
        "boosterRemainingHours",
        "boosterRemainingMinutes",
        "boosterRemainingSeconds",
        "boosterBonusOverride",
        "Применить время",
        "copyEvePlanBtn",
        "summaryCopyEveBtn",
        "summaryCopyMessage",
        "Скопировать для EVE",
    ):
        assert marker in response.text


def test_static_js_contains_eve_clipboard_copy_flow():
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "copyEvePlan" in response.text
    assert "eve_clipboard_line" in response.text
    assert "Импортировать навыки из буфера обмена" in response.text


def test_ui_has_compact_header_single_implant_and_nes_controls():
    response = client.get("/")
    text = response.text
    assert 'id="versionText"' in text
    assert 'id="activeCharacterLine"' in text
    assert 'id="implantScenario"' in text
    assert 'class="implantScenario"' not in text
    assert 'id="includeNesAccelerators"' in text
    assert 'id="nesAcceleratorBonus"' in text
    assert 'id="nesAcceleratorMode"' in text
    assert 'id="nesAcceleratorCount"' in text
    assert 'id="nesAcceleratorCountField" hidden' in text
    assert "Количество ускорителей" in text
    assert 'id="largeInjectorMode"' in text
    assert 'id="comboLargeInjectorCountField" hidden' in text
    assert 'id="comboLargeInjectorCount"' in text
    assert 'id="largeInjectorModeHint"' in text
    assert 'id="manualPlexPrice"' in text
    assert 'id="operationProgress"' in text


def test_schedule_ui_hides_shared_metrics_from_main_summary():
    response = client.get("/static/app.js")
    assert '["Общих этапов", data.summary?.shared_tasks]' not in response.text
    assert '["Общих SP", formatSP(data.summary?.shared_unique_sp)]' not in response.text
    assert "Самый быстрый практичный" in response.text
    assert "Справочно: закрыть все оставшиеся SP Large Skill Injectors" in response.text


def test_ui_contains_progress_and_human_time_comparison():
    js = client.get("/static/app.js").text
    assert "startOperationProgress" in js
    assert "renderSummaryProgress" in js
    assert "Разница с оптимизированной" in js
    assert "Оптимизированная очередь:" in js
    assert '["Рекомендованная очередь", overall, "—"]' not in js
    assert "formatTimeDifferenceFromOptimized" in js
    assert "Оценка порядка" not in js
    assert "Самый дешёвый полезный" in js


def test_economics_ui_explains_lsi_auto_and_manual_modes():
    html = client.get("/").text
    js = client.get("/static/app.js").text
    assert "Автоподбор" in html
    assert "Вручную" in html
    assert "Автоподбор сам рассчитает количество LSI" in html
    assert "updateLargeInjectorControls" in js
    assert "Результат автоподбора LSI" in js
    assert "SP от LSI" in js
    assert "Осталось обучить" in js
    assert "ISK за сэкономленный день" in js
    assert "Подробности расчётов" in js
    assert 'table(["Способ", "До завершения", "Экономия времени", "Итого", "ISK за сэкономленный день"]' in js


def test_lsi_mode_switch_is_visible_and_old_hidden_count_is_not_reused():
    js = client.get("/static/app.js").text
    html = client.get("/").text
    assert 'if (id === "largeInjectorMode") updateLargeInjectorControls();' in js
    assert 'savedUiSchemaVersion >= uiSchemaVersion ? (saved.comboLargeInjectorCount || 0) : 0' in js
    assert 'Режим Large Skill Injectors' in html
    assert 'Количество Large Skill Injectors вручную' in html
    assert 'Результат автоподбора LSI' in js
    assert 'NES +${formatInteger(bonus)} · без LSI' in js


def test_economics_summary_does_not_join_multiple_nes_pareto_variants():
    js = client.get("/static/app.js").text
    assert 'Лучшие практичные варианты цена / время", escapeHtml(frontierLabels)' not in js
    assert 'const frontierLabels =' not in js


def test_economics_frontend_ignores_stale_recalculation_responses_and_uses_server_echo():
    js = client.get("/static/app.js").text
    assert "economicsRequestSeq" in js
    assert "requestSeq !== state.economicsRequestSeq" in js
    assert "data.large_skill_injectors || {}" in js
    assert "selection.requested_count" in js
    assert "при текущей цене ни один LSI не снижает стоимость одного сэкономленного дня" in js


def test_economics_settings_do_not_auto_recalculate():
    html = client.get("/").text
    js = client.get("/static/app.js").text
    assert 'id="economicsSettingsMessage"' in html
    assert "markEconomicsDirty" in js
    assert "Настройки изменены. Нажми «Рассчитать всё»" in js
    assert "scheduleEconomicsRecalc" not in js
    assert "economicsRecalcTimer" not in js

