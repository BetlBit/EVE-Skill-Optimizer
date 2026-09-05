const $ = (id) => document.getElementById(id);

const state = {
  plans: [],
  last: {},
  characters: [],
  operation: { timer: null, startedAt: null },
  economicsRequestSeq: 0,
};

const storageKey = "eveSkillOptimizer.ui.v2";
const uiSchemaVersion = 5;

const objectiveHelp = {
  weighted_completion_time: "Приоритетные цели стараются завершиться как можно раньше. Это основной режим для нескольких целей разной важности.",
  priority_lexicographic: "Сначала максимально быстро закрывается цель с самым высоким приоритетом, затем следующая.",
  makespan: "Минимизируется общее время до полного завершения всех выбранных целей.",
};

const strategyLabels = {
  no_changes: "Без ускорений (базовое время)",
  time_optimal_no_purchase: "Максимальное ускорение без покупок",
  implants_3: "Импланты +3",
  implants_4: "Импланты +4",
  implants_5: "Импланты +5",
  large_injector_all_remaining: "Справочно: закрыть все оставшиеся SP Large Skill Injectors",
  large_injector_highest_priority_milestone: "Large Skill Injectors для главной цели",
  large_injector_hybrid_half_remaining: "Large Skill Injectors: примерно половина оставшихся SP",
  small_injector_all_remaining: "Справочно: закрыть все оставшиеся SP Small Skill Injectors",
  small_injector_highest_priority_milestone: "Small Skill Injectors для главной цели",
  small_injector_hybrid_half_remaining: "Small Skill Injectors: примерно половина оставшихся SP",
  market_accelerators_unavailable: "Рыночные ускорители",
  plex_offers_unavailable: "Варианты за PLEX",
};

const attributeLabels = {
  intelligence: "INT",
  memory: "MEM",
  perception: "PER",
  willpower: "WIL",
  charisma: "CHA",
};

const boosterSourceLabels = {
  manual_override: "указано вручную",
  evemon_direct: "получено из EVEMon",
  evemon_active_booster_expires_at: "получено из EVEMon",
  inferred_from_evemon_skill_queue: "рассчитано по очереди EVEMon",
  ignored_by_user: "исключён пользователем из расчёта",
};

const confidenceLabels = {
  high: "высокая",
  medium: "средняя",
  low: "низкая",
  user: "указано пользователем",
};

document.addEventListener("DOMContentLoaded", () => {
  bindTabs();
  bindActions();
  loadUiState();
  updateNesControls();
  updateLargeInjectorControls();
  renderPlans();
  renderObjectiveHelp();
  refreshStatus().catch(showTopError);
  window.setInterval(() => {
    if ($("boosterExpiryOverride")?.value) {
      renderBoosterStatus(state.last.schedule?.boosters || state.last.time?.boosters || state.last.analyze?.boosters, state.last.status?.character);
    }
  }, 1000);
});

function bindTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
}

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabName));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
  $(`tab-${tabName}`)?.classList.add("active");
  saveUiState();
  if (tabName === "summary") renderSummary();
}

function bindActions() {
  $("refreshStatusBtn").addEventListener("click", () => runButton("refreshStatusBtn", null, refreshStatus));
  $("loadCharacterBtn").addEventListener("click", () => runButton("loadCharacterBtn", null, refreshStatus));
  $("updateSdeBtn").addEventListener("click", () => runButton("updateSdeBtn", "sdeMessage", updateSde));
  $("findCharactersBtn").addEventListener("click", () => runButton("findCharactersBtn", "evemonMessage", findCharacters));
  $("addPlanBtn").addEventListener("click", () => addPlan());
  $("duplicatePlanBtn").addEventListener("click", duplicateLastPlan);
  $("clearPlansBtn").addEventListener("click", clearPlans);
  $("analyzeBtn").addEventListener("click", () => runButton("analyzeBtn", "analysisResult", runAnalyze));
  $("scheduleBtn").addEventListener("click", () => runButton("scheduleBtn", "scheduleResult", runSchedule));
  $("copyEvePlanBtn").addEventListener("click", () => runButton("copyEvePlanBtn", "scheduleCopyMessage", copyEvePlan));
  $("summaryCopyEveBtn").addEventListener("click", () => runButton("summaryCopyEveBtn", "summaryResult", copyEvePlan));
  $("timeBtn").addEventListener("click", () => runButton("timeBtn", "timeResult", runTimeOptimization));
  $("economicsBtn").addEventListener("click", () => runButton("economicsBtn", "economicsResult", runEconomics));
  $("calculateAllBtn").addEventListener("click", () => runButton("calculateAllBtn", "summaryResult", calculateAll));
  $("summaryCalculateAllBtn").addEventListener("click", () => runButton("summaryCalculateAllBtn", "summaryResult", calculateAll));
  $("copyJsonBtn").addEventListener("click", copyJson);
  $("downloadJsonBtn").addEventListener("click", downloadJson);
  $("downloadScheduleCsvBtn").addEventListener("click", downloadScheduleCsv);
  $("downloadEconomicsCsvBtn").addEventListener("click", downloadEconomicsCsv);
  $("applyBoosterRemainingBtn").addEventListener("click", () => {
    try {
      applyBoosterRemainingTime();
    } catch (error) {
      const target = $("boosterStatus");
      if (target) {
        target.className = "notice blocking";
        target.innerHTML = `<strong>Не удалось применить время бустера.</strong><br>${escapeHtml(error.message)}`;
      }
    }
  });
  $("clearBoosterOverrideBtn").addEventListener("click", () => {
    $("boosterExpiryOverride").value = "";
    clearBoosterRemainingInputs();
    saveUiState();
    renderBoosterStatus(state.last.schedule?.boosters || state.last.time?.boosters || state.last.analyze?.boosters, state.last.status?.character);
  });

  [
    "objective",
    "respectQueue",
    "useImported",
    "economicObjective",
    "includeLargeInjectors",
    "includeSmallInjectors",
    "includeNesAccelerators",
    "nesAcceleratorBonus",
    "nesAcceleratorMode",
    "nesAcceleratorCount",
    "largeInjectorMode",
    "comboLargeInjectorCount",
    "manualPlexPrice",
    "useUnallocatedSp",
    "evemonPath",
    "boosterExpiryOverride",
    "boosterBonusOverride",
    "boosterRemainingHours",
    "boosterRemainingMinutes",
    "boosterRemainingSeconds",
    "ignoreBooster",
  ].forEach((id) => {
    $(id)?.addEventListener("change", () => {
      if (id === "objective") renderObjectiveHelp();
      if (id === "nesAcceleratorMode") updateNesControls();
      if (id === "largeInjectorMode") updateLargeInjectorControls();
      if (id === "boosterExpiryOverride" && $("boosterExpiryOverride").value) {
        $("ignoreBooster").checked = false;
        clearBoosterRemainingInputs();
      }
      if (id === "boosterBonusOverride" && $("boosterBonusOverride").value) $("ignoreBooster").checked = false;
      if (id === "ignoreBooster" && $("ignoreBooster").checked) {
        $("boosterExpiryOverride").value = "";
        clearBoosterRemainingInputs();
      }
      saveUiState();
      if (["economicObjective", "includeLargeInjectors", "includeSmallInjectors", "includeNesAccelerators", "nesAcceleratorBonus", "nesAcceleratorMode", "nesAcceleratorCount", "largeInjectorMode", "comboLargeInjectorCount", "manualPlexPrice", "useUnallocatedSp"].includes(id)) {
        markEconomicsDirty();
      }
      if (["boosterExpiryOverride", "boosterBonusOverride", "boosterRemainingHours", "boosterRemainingMinutes", "boosterRemainingSeconds", "ignoreBooster"].includes(id)) {
        renderBoosterStatus(state.last.schedule?.boosters || state.last.time?.boosters || state.last.analyze?.boosters, state.last.status?.character);
      }
    });
  });
  $("implantScenario")?.addEventListener("change", () => { saveUiState(); markEconomicsDirty(); });
  ["comboLargeInjectorCount", "manualPlexPrice", "nesAcceleratorCount"].forEach((id) => {
    $(id)?.addEventListener("input", () => { saveUiState(); markEconomicsDirty(); });
  });

  document.addEventListener("click", (event) => {
    const open = event.target.closest("[data-open-booster]");
    if (open) {
      switchTab("character");
      $("boosterSettingsPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
      $("boosterRemainingHours")?.focus();
    }
    const ignore = event.target.closest("[data-ignore-booster]");
    if (ignore) {
      $("ignoreBooster").checked = true;
      $("boosterExpiryOverride").value = "";
      clearBoosterRemainingInputs();
      saveUiState();
      switchTab("character");
      renderBoosterStatus(state.last.schedule?.boosters || state.last.time?.boosters || state.last.analyze?.boosters, state.last.status?.character);
    }
  });
}

function updateNesControls() {
  const field = $("nesAcceleratorCountField");
  if (!field) return;
  field.hidden = $("nesAcceleratorMode")?.value !== "count";
}

function updateLargeInjectorControls() {
  const mode = $("largeInjectorMode")?.value || "auto";
  const countField = $("comboLargeInjectorCountField");
  const hint = $("largeInjectorModeHint");
  if (countField) countField.hidden = mode !== "manual";
  if (hint) {
    hint.textContent = mode === "auto"
      ? "Автоподбор сам рассчитает количество LSI. Выбранное число и результат будут показаны ниже."
      : "Укажи количество LSI вручную. Значение 0 означает: не использовать инжекторы.";
  }
}

function renderObjectiveHelp() {
  $("objectiveHelp").textContent = objectiveHelp[$("objective").value] || "";
}

async function refreshStatus() {
  const data = await api("/api/status");
  state.last.status = data;
  if ($("versionText")) $("versionText").textContent = `версия ${data.version || "—"}`;
  if ($("activeCharacterLine")) $("activeCharacterLine").textContent = `Персонаж: ${data.character?.character_name || "не выбран"}`;
  renderSdeStatus(data.sde);
  renderCharacter(data.character);
  renderBoosterStatus(state.last.schedule?.boosters || state.last.time?.boosters || state.last.analyze?.boosters, data.character);
  setRaw("status", data);
  saveUiState();
  renderSummary();
  return data;
}

async function updateSde() {
  setMessage("sdeMessage", "Обновляем SDE…");
  const data = await api("/api/sde/update", { method: "POST" });
  setMessage("sdeMessage", `SDE загружена: версия ${data.build}, ${formatInteger(data.skills_loaded)} навыков`, "ok");
  await refreshStatus();
  return data;
}

async function findCharacters() {
  const path = $("evemonPath").value.trim();
  if (!path) throw new Error("Укажи путь к резервной копии EVEMon.");
  localStorage.setItem("eveSkillOptimizer.evemonPath", path);
  const data = await api("/api/evemon/characters", { method: "POST", body: { path } });
  state.characters = data.characters || [];
  renderCharacters();
  setRaw("evemonCharacters", data);
  return data;
}

async function importCharacter(characterId) {
  const path = $("evemonPath").value.trim();
  const data = await api("/api/evemon/import", { method: "POST", body: { path, character_id: characterId } });
  setMessage("evemonMessage", `Импортирован персонаж: ${data.character.character_name || data.character.character_id}`, "ok");
  setRaw("evemonImport", data);
  await refreshStatus();
  return data;
}

function renderSdeStatus(sde) {
  $("sdeStatus").innerHTML = kvGrid([
    ["Статус", sde.loaded ? `<span class="ok">Загружена</span>` : `<span class="danger">Не загружена</span>`],
    ["Версия SDE", sde.build || "Недоступна"],
    ["Навыков", formatInteger(sde.skills_loaded || 0)],
  ]);
}

function renderCharacters() {
  if (!state.characters.length) {
    $("charactersList").innerHTML = `<p class="muted">Персонажи не найдены.</p>`;
    return;
  }
  $("charactersList").innerHTML = state.characters.map((c) => `
    <article class="character-card">
      <div class="character-card-title">${escapeHtml(c.name || c.character_name || `ID ${c.character_id}`)}</div>
      <div class="character-card-meta">
        <span>${formatInteger(c.skills_count || 0)} навыков</span>
        <span>${formatSP(c.allocated_sp || c.total_sp || 0)} изучено</span>
        <span>${formatSP(c.unallocated_sp || 0)} свободно</span>
      </div>
      <button type="button" data-import="${Number(c.character_id)}">Импортировать</button>
    </article>
  `).join("");
  $("charactersList").querySelectorAll("[data-import]").forEach((button) => {
    button.addEventListener("click", () => runButton(button, "evemonMessage", () => importCharacter(Number(button.dataset.import))));
  });
}

function renderCharacter(character) {
  if (!character) {
    $("characterState").innerHTML = `<p class="muted">Персонаж ещё не импортирован.</p>`;
    return;
  }
  const attrs = character.base_attributes || character.attributes || {};
  $("characterState").innerHTML = kvGrid([
    ["Персонаж", escapeHtml(character.character_name || "Без имени")],
    ["ID персонажа", formatInteger(character.character_id)],
    ["Всего SP", formatSP(character.total_sp)],
    ["Свободные SP", formatSP(character.unallocated_sp)],
    ["Базовые атрибуты", formatAttributes(attrs)],
    ["Импланты", formatAttributes(character.implant_attribute_bonus || {})],
    ["Бустер", formatAttributes(character.booster_attribute_bonus || {})],
    ["Итоговые атрибуты", formatAttributes(character.effective_attributes || {})],
    ["Бонусные ремапы", nullable(character.bonus_remaps)],
    ["Очередь навыков", `${(character.skill_queue || []).length} поз.`],
  ]);
}

function renderBoosterStatus(boosters, character) {
  const target = $("boosterStatus");
  if (!target) return;

  const importedBonus = boosters?.imported_attribute_bonus || character?.booster_attribute_bonus || {};
  const effectiveFromApi = boosters?.effective_attribute_bonus || boosters?.attribute_bonus || importedBonus;
  const manualStrength = manualBoosterStrength();
  const effectiveStrength = manualStrength || uniformBoosterStrength(effectiveFromApi) || uniformBoosterStrength(importedBonus);
  renderDetectedBoosterStrength(importedBonus, manualStrength);

  if ($("ignoreBooster")?.checked) {
    target.className = "notice info";
    target.innerHTML = `<strong>Бустер исключён из расчётов.</strong><br>Импортированные данные не удалены. Сними флажок, чтобы снова учитывать бустер.`;
    return;
  }

  const manual = $("boosterExpiryOverride")?.value;
  if (manual) {
    const iso = localDateTimeToIso(manual);
    const remaining = formatBoosterRemaining(iso);
    if (!effectiveStrength) {
      target.className = "notice warning-notice";
      target.innerHTML = `<strong>Время бустера указано, но его сила неизвестна.</strong><br>Осталось: ${escapeHtml(remaining)}. Укажи силу бустера, например +8, чтобы расчёт был точным.`;
      return;
    }
    target.className = "notice ok-notice";
    target.innerHTML = `<strong>Бустер задан для расчёта: +${formatInteger(effectiveStrength)} ко всем атрибутам.</strong><br>Осталось: ${escapeHtml(remaining)} · окончание: ${formatDate(iso)}.`;
    return;
  }

  const hasAttributeBooster = Object.values(importedBonus || {}).some((v) => Number(v) > 0) || !!manualStrength;
  if (!hasAttributeBooster) {
    target.className = "notice warning-notice";
    target.innerHTML = `<strong>Сила активного attribute booster не определена.</strong><br>Если бустер активен в EVE, укажи оставшееся время и его силу вручную.`;
    return;
  }
  if (boosters?.ignored) {
    target.className = "notice info";
    target.innerHTML = `<strong>Бустер исключён из текущего расчёта.</strong>`;
    return;
  }
  if (boosters?.expires_at) {
    const source = boosterSourceLabels[boosters.expiry_source] || "определено программой";
    const confidence = boosters.expiry_confidence ? ` · уверенность: ${confidenceLabels[boosters.expiry_confidence] || boosters.expiry_confidence}` : "";
    const strength = effectiveStrength ? `+${formatInteger(effectiveStrength)} ко всем атрибутам · ` : "";
    target.className = "notice ok-notice";
    target.innerHTML = `<strong>Бустер учитывается: ${strength}${escapeHtml(formatBoosterRemaining(boosters.expires_at))}.</strong><br>Окончание: ${formatDate(boosters.expires_at)} · источник: ${escapeHtml(source)}${escapeHtml(confidence)}.`;
    return;
  }
  target.className = "notice warning-notice";
  target.innerHTML = `
    <strong>Время окончания бустера не определено.</strong><br>
    ${effectiveStrength ? `Сила бустера: +${formatInteger(effectiveStrength)} ко всем атрибутам. ` : ""}Введи оставшиеся часы, минуты и секунды ровно как в EVE. После этого точные ремапы и сравнение ISK/время снова станут доступны.<br>
    <span class="notice-actions"><button type="button" data-open-booster>Указать время и силу</button><button type="button" data-ignore-booster>Игнорировать бустер</button></span>
  `;
}

function manualBoosterStrength() {
  const raw = $("boosterBonusOverride")?.value?.trim();
  if (!raw) return null;
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function uniformBoosterStrength(attrs = {}) {
  const values = ["intelligence", "memory", "perception", "willpower", "charisma"].map((key) => Number(attrs?.[key] || 0));
  if (!values.some((value) => value > 0)) return null;
  return values.every((value) => value === values[0]) ? values[0] : null;
}

function renderDetectedBoosterStrength(importedBonus = {}, manualStrength = null) {
  const target = $("boosterDetectedStrength");
  if (!target) return;
  const detected = uniformBoosterStrength(importedBonus);
  if (manualStrength) {
    target.textContent = `В расчётах используется ручная сила: +${manualStrength} ко всем атрибутам${detected ? `. EVEMon передал +${detected}.` : "."}`;
  } else if (detected) {
    target.textContent = `EVEMon передал силу: +${detected} ко всем атрибутам. Поле можно оставить пустым или исправить значение вручную.`;
  } else {
    target.textContent = "EVEMon не передал однозначную силу бустера. Укажи число вручную, например 8 для +8 ко всем атрибутам.";
  }
}

function clearBoosterRemainingInputs() {
  ["boosterRemainingHours", "boosterRemainingMinutes", "boosterRemainingSeconds"].forEach((id) => {
    if ($(id)) $(id).value = "";
  });
}

function applyBoosterRemainingTime() {
  const hours = Number($("boosterRemainingHours").value || 0);
  const minutes = Number($("boosterRemainingMinutes").value || 0);
  const seconds = Number($("boosterRemainingSeconds").value || 0);
  if (![hours, minutes, seconds].every(Number.isInteger) || hours < 0 || minutes < 0 || minutes > 59 || seconds < 0 || seconds > 59) {
    throw new Error("Проверь оставшееся время бустера: часы ≥ 0, минуты и секунды — от 0 до 59.");
  }
  const totalSeconds = hours * 3600 + minutes * 60 + seconds;
  if (totalSeconds <= 0) throw new Error("Оставшееся время бустера должно быть больше нуля.");
  const expiresAt = new Date(Date.now() + totalSeconds * 1000);
  $("boosterExpiryOverride").value = dateToLocalInputValue(expiresAt);
  $("ignoreBooster").checked = false;
  clearBoosterRemainingInputs();
  saveUiState();
  renderBoosterStatus(state.last.schedule?.boosters || state.last.time?.boosters || state.last.analyze?.boosters, state.last.status?.character);
}

function dateToLocalInputValue(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatBoosterRemaining(value) {
  if (!value) return "не определено";
  const end = new Date(value).getTime();
  if (!Number.isFinite(end)) return "не определено";
  let seconds = Math.max(0, Math.floor((end - Date.now()) / 1000));
  const hours = Math.floor(seconds / 3600);
  seconds %= 3600;
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${hours} ч ${minutes} мин ${secs} сек`;
}

function addPlan(plan = { name: `Цель ${state.plans.length + 1}`, priority: 1, text: "" }) {
  state.plans.push(plan);
  renderPlans();
  saveUiState();
}

function duplicateLastPlan() {
  const last = state.plans[state.plans.length - 1] || { name: "Цель", priority: 1, text: "" };
  addPlan({ name: `${last.name} — копия`, priority: last.priority, text: last.text });
}

function clearPlans() {
  state.plans = [];
  renderPlans();
  saveUiState();
}

function renderPlans() {
  if (!state.plans.length) {
    $("planList").innerHTML = `<div class="empty-state"><strong>Целей пока нет.</strong><br>Нажми «Добавить цель», укажи желаемые навыки и приоритет.</div>`;
    return;
  }
  $("planList").innerHTML = state.plans.map((plan, index) => `
    <article class="plan-card" data-plan="${index}">
      <div class="plan-head-row">
        <label class="field"><span>Название цели</span><input data-field="name" value="${escapeAttr(plan.name)}" required></label>
        <label class="field priority-field"><span>Приоритет</span><input data-field="priority" type="number" min="0.1" step="0.1" value="${Number(plan.priority) || 1}" title="Чем выше число, тем важнее быстрее завершить эту цель."></label>
        <button type="button" data-remove="${index}">Удалить</button>
      </div>
      <label class="field"><span>Необходимые навыки</span><textarea data-field="text" placeholder="Marauders IV&#10;Amarr Battleship V&#10;или вставь экспорт из EVE / EVEMon">${escapeHtml(plan.text || "")}</textarea></label>
      <p class="field-hint">Формат: <code>Marauders IV</code>, <code>Marauders 4</code> или вставка из EVE/EVEMon.</p>
      <p class="field-hint">Чем выше приоритет, тем сильнее оптимизатор стремится завершить эту цель раньше. Это не обязательно означает полный запрет на вставку навыков из других целей.</p>
      <p class="message error" data-error="${index}"></p>
    </article>
  `).join("");
  $("planList").querySelectorAll("input, textarea").forEach((input) => input.addEventListener("input", updatePlanFromDom));
  $("planList").querySelectorAll("[data-remove]").forEach((button) => button.addEventListener("click", () => {
    state.plans.splice(Number(button.dataset.remove), 1);
    renderPlans();
    saveUiState();
  }));
}

function updatePlanFromDom() {
  state.plans = [...$("planList").querySelectorAll(".plan-card")].map((card) => ({
    name: card.querySelector('[data-field="name"]').value,
    priority: Number(card.querySelector('[data-field="priority"]').value),
    text: card.querySelector('[data-field="text"]').value,
  }));
  saveUiState();
}

function buildPlanPayload() {
  updatePlanFromDom();
  const errors = [];
  state.plans.forEach((plan, index) => {
    const messages = [];
    if (!plan.name.trim()) messages.push("Укажи название цели");
    if (!(Number(plan.priority) > 0)) messages.push("Приоритет должен быть больше 0");
    if (!plan.text.trim()) messages.push("Добавь хотя бы один навык");
    const target = document.querySelector(`[data-error="${index}"]`);
    if (target) target.textContent = messages.join("; ");
    if (messages.length) errors.push(...messages);
  });
  if (errors.length) throw new Error("Исправь ошибки в целях обучения.");
  return state.plans.map((p) => ({ name: p.name.trim(), text: p.text.trim(), priority: Number(p.priority) }));
}

function boosterPayload() {
  if ($("ignoreBooster").checked) return { ignore_active_booster: true };
  const payload = { ignore_active_booster: false };
  const strength = manualBoosterStrength();
  if ($("boosterBonusOverride").value.trim() && !strength) throw new Error("Сила бустера должна быть целым положительным числом.");
  if (strength) payload.booster_attribute_bonus_override = strength;
  const localValue = $("boosterExpiryOverride").value;
  if (localValue) {
    const iso = localDateTimeToIso(localValue);
    if (!iso) throw new Error("Некорректное время окончания бустера.");
    if (new Date(iso).getTime() <= Date.now()) throw new Error("Время окончания бустера уже прошло. Укажи актуальное оставшееся время.");
    payload.booster_expires_at_override = iso;
  }
  return payload;
}

function commonPayload() {
  return {
    plans: buildPlanPayload(),
    current_skills: [],
    use_imported_character: $("useImported").checked,
    omega: true,
    objective: $("objective").value,
    respect_current_queue: $("respectQueue").checked,
    ...boosterPayload(),
  };
}

function analyzePayload() {
  const common = commonPayload();
  return {
    plans: common.plans,
    current_skills: common.current_skills,
    use_imported_character: common.use_imported_character,
    omega: common.omega,
    implant_bonus: 0,
    booster_expires_at_override: common.booster_expires_at_override,
    booster_attribute_bonus_override: common.booster_attribute_bonus_override,
    ignore_active_booster: common.ignore_active_booster,
  };
}

function selectedImplantScenarios() {
  return [$("implantScenario")?.value || "current"];
}

async function runAnalyze() {
  const data = await api("/api/analyze", { method: "POST", body: analyzePayload() });
  state.last.analyze = data;
  setRaw("analyze", data);
  $("analysisResult").innerHTML = [
    summaryGrid([
      ["Источник", data.character_source === "evemon" ? "EVEMon" : "Введено вручную"],
      ["Осталось SP", formatSP(data.remaining_sp)],
      ["Этапов обучения", formatInteger(data.task_count)],
      ["При текущих условиях", durationCell(data.current_state)],
      ["С лучшим одиночным ремапом", durationCell(data.best_single_remap)],
      ["Экономия", formatDuration(data.best_single_remap?.time_saved_seconds_vs_current)],
    ]),
    boosterGuidance(data.boosters),
    h3("Навыки к изучению"),
    table(["Навык", "Уровень", "SP", "Основной атрибут", "Вторичный атрибут"], (data.tasks || []).map((t) => [
      escapeHtml(t.skill),
      romanOrNumber(t.level),
      formatSP(t.sp_remaining),
      attributeName(t.primary),
      attributeName(t.secondary),
    ])),
  ].join("");
  renderBoosterStatus(data.boosters, state.last.status?.character);
  renderSummary();
  return data;
}

async function runSchedule() {
  const data = await api("/api/schedule", { method: "POST", body: commonPayload() });
  state.last.schedule = data;
  setRaw("schedule", data);
  $("scheduleResult").innerHTML = renderSchedule(data);
  renderBoosterStatus(data.boosters, state.last.status?.character);
  renderSummary();
  return data;
}

async function runTimeOptimization() {
  const data = await api("/api/optimize-training", {
    method: "POST",
    body: { ...commonPayload(), implant_scenarios: selectedImplantScenarios() },
  });
  state.last.time = data;
  setRaw("time", data);
  $("timeResult").innerHTML = renderTime(data);
  renderBoosterStatus(data.boosters, state.last.status?.character);
  renderSummary();
  return data;
}

async function runEconomics() {
  const requestSeq = ++state.economicsRequestSeq;
  const lsiMode = $("largeInjectorMode").value;
  const payload = {
    ...commonPayload(),
    implant_scenarios: selectedImplantScenarios(),
    include_large_skill_injectors: $("includeLargeInjectors").checked,
    include_small_skill_injectors: $("includeSmallInjectors").checked,
    include_market_accelerators: false,
    include_plex_offers: false,
    include_nes_accelerators: $("includeNesAccelerators").checked,
    nes_accelerator_bonus: Number($("nesAcceleratorBonus").value || 12),
    nes_accelerator_mode: $("nesAcceleratorMode").value,
    nes_accelerator_count: Number($("nesAcceleratorCount").value || 1),
    combo_large_injector_count: lsiMode === "manual" ? Math.max(0, Number($("comboLargeInjectorCount").value || 0)) : 0,
    optimize_large_injector_count: lsiMode === "auto",
    plex_unit_price_isk_override: $("manualPlexPrice").value ? Number($("manualPlexPrice").value) : null,
    use_unallocated_sp: $("useUnallocatedSp").checked,
    economic_objective: $("economicObjective").value,
  };
  const data = await api("/api/optimize-economics", { method: "POST", body: payload });
  // Several debounced recalculations can overlap. Never let an older response
  // overwrite a newer manual LSI value.
  if (requestSeq !== state.economicsRequestSeq) return data;
  state.last.economics = data;
  setRaw("economics", data);
  setMessage("economicsSettingsMessage", "", "");
  $("economicsResult").innerHTML = renderEconomics(data);
  if (data.boosters) renderBoosterStatus(data.boosters, state.last.status?.character);
  renderSummary();
  return data;
}

async function calculateAll() {
  const started = Date.now();
  renderSummaryProgress(1, 3, "Очередь навыков", started);
  setOperationProgress("Полный расчёт", "Этап 1 из 3: очередь навыков", 10);
  const schedule = await runSchedule();
  renderSummaryProgress(2, 3, "Атрибуты, импланты и ремапы", started);
  setOperationProgress("Полный расчёт", "Этап 2 из 3: атрибуты, импланты и ремапы", 45);
  const time = await runTimeOptimization();
  renderSummaryProgress(3, 3, "Стоимость ускорения", started);
  setOperationProgress("Полный расчёт", "Этап 3 из 3: стоимость ускорения", 75);
  const economics = await runEconomics();
  setOperationProgress("Полный расчёт", "Готово", 100);
  renderSummary();
  switchTab("summary");
  return { schedule, time, economics };
}

function renderSummaryProgress(step, total, label, startedAt) {
  const target = $("summaryResult");
  if (!target) return;
  const percent = Math.max(1, Math.min(99, Math.round(((step - 0.5) / total) * 100)));
  const elapsed = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
  target.innerHTML = `<div class="notice info summary-progress"><strong>Выполняется полный расчёт</strong><div class="progress-track"><div class="progress-bar" style="width:${percent}%"></div></div><div>Этап ${step} из ${total}: ${escapeHtml(label)}</div><div class="field-hint">Прошло: ${elapsed}с · программа работает, оставшееся время зависит от сложности целей.</div></div>`;
}


function renderSchedule(data) {
  const overall = data.exact
    ? maxPlanCompletion(data.plans)
    : rangeDuration(data.range?.best_case_all_plans_seconds, data.range?.worst_case_all_plans_seconds);
  const planRows = (data.plans || []).map((p) => [
    escapeHtml(p.name),
    formatNumber(p.priority),
    formatSP(p.remaining_sp),
    p.completion_seconds != null
      ? formatDuration(p.completion_seconds)
      : rangeDuration(p.completion_best_case_seconds, p.completion_worst_case_seconds),
    p.completion_at
      ? formatDate(p.completion_at)
      : rangeDate(p.completion_best_case_at, p.completion_worst_case_at),
    p.completed_after_task_index ?? "—",
  ]);

  const recommendedSeconds = data.exact ? Number(data.optimizer?.all_plans_completion_seconds ?? Math.max(0, ...(data.plans || []).map((p) => Number(p.completion_seconds || 0)))) : null;
  const baseline = data.exact ? [
    ["Исходный порядок целей", formatDuration(data.baselines?.input_plan_order?.all_plans_completion_seconds), formatTimeDifferenceFromOptimized(data.baselines?.input_plan_order?.all_plans_completion_seconds, recommendedSeconds)],
    ["Сначала самый короткий доступный навык", formatDuration(data.baselines?.shortest_available_task?.all_plans_completion_seconds), formatTimeDifferenceFromOptimized(data.baselines?.shortest_available_task?.all_plans_completion_seconds, recommendedSeconds)],
  ] : [];

  return [
    summaryGrid([
      ["Целей", data.summary?.plans_count],
      ["Уникальных этапов", data.summary?.unique_tasks],
      ["Осталось SP", formatSP(data.summary?.remaining_sp)],
      ["До завершения всех целей", overall],
    ]),
    boosterGuidance(data.boosters),
    h3("Готовность целей"),
    table(["Цель", "Приоритет", "SP", "Через сколько", "Дата готовности", "№ в очереди"], planRows),
    h3("Рекомендованная очередь"),
    scheduleTable(data.recommended_schedule || []),
    data.exact ? h3("Сравнение с обычным порядком") + `<div class="comparison-baseline"><strong>Оптимизированная очередь:</strong> ${overall}</div>` + table(["Вариант", "Все цели", "Разница с оптимизированной"], baseline) : "",
    technicalDetails("Дополнительно", [
      ["Метод расчёта", optimizerAlgorithmLabel(data.optimizer?.algorithm)],
      ["Точность", optimizerOptimalityLabel(data.optimizer?.optimality)],
      ["Проверено состояний", formatInteger(data.optimizer?.states_evaluated || 0)],
      ["Внутренняя целевая функция", formatNumber(data.optimizer?.objective_value)],
    ]),
  ].join("");
}

function renderTime(data) {
  const baseSeconds = data.exact
    ? scheduleSeconds(data.no_changes?.schedule)
    : null;
  const baseText = data.exact
    ? formatDuration(baseSeconds)
    : rangeDuration(data.range?.best_case_all_plans_seconds, data.range?.worst_case_all_plans_seconds);
  const recommendedScenario = data.recommended?.blocked ? "Не рассчитано" : scenarioLabel(data.recommended?.scenario);

  const scenarioRows = (data.scenarios || []).map((s) => {
    const allPlans = s.exact
      ? formatDuration(s.all_plans_completion_seconds)
      : rangeDuration(s.range?.best_case_all_plans_seconds, s.range?.worst_case_all_plans_seconds);
    const saved = s.exact && baseSeconds != null && s.all_plans_completion_seconds != null
      ? formatDuration(baseSeconds - s.all_plans_completion_seconds)
      : "Не рассчитывается точно";
    return [
      scenarioLabel(s.scenario),
      formatAttributes(s.implant_attribute_bonus),
      s.required_cybernetics_level ? `Cybernetics ${romanOrNumber(s.required_cybernetics_level)}` : "Не требуется",
      s.implant_activates_at ? formatDate(s.implant_activates_at) : (s.exact ? "Сразу / после подготовки" : "Зависит от окончания бустера"),
      s.exact ? ((s.remap_actions || []).map((r) => remapKind(r.kind)).join(", ") || "Без ремапа") : "Не рекомендуется до уточнения бустера",
      allPlans,
      s.exact ? firstPlanDuration(s) : "Диапазон",
      saved,
    ];
  });

  const remapRows = data.exact
    ? (data.scenarios || []).flatMap((s) => (s.remap_actions || []).map((r) => [
      scenarioLabel(s.scenario),
      r.index,
      formatDate(r.occurs_at),
      remapKind(r.kind),
      formatAttributes(r.base_attributes),
    ]))
    : [];

  const recommendationNote = data.exact
    ? `<div class="notice ok-notice"><strong>Рекомендуемый сценарий: ${escapeHtml(recommendedScenario)}.</strong><br>Он даёт минимальное время обучения среди выбранных сценариев с учётом времени на необходимый Cybernetics и доступных ремапов. Стоимость покупки имплантов здесь ещё не учитывается — она сравнивается во вкладке «Стоимость».</div>`
    : `<div class="notice blocking"><strong>Рекомендация по ремапу временно недоступна.</strong><br>Точное окончание бустера неизвестно. Программа показывает диапазон времени, но не предлагает тратить обычный или бонусный ремап на основе неопределённых данных.<br><span class="notice-actions"><button type="button" data-open-booster>Указать окончание бустера</button><button type="button" data-ignore-booster>Игнорировать бустер</button></span></div>`;

  return [
    summaryGrid([
      ["Без изменений", baseText],
      ["Обычный ремап", data.remap_availability?.timed_available_now ? `<span class="ok">Доступен</span>` : `Доступен: ${formatDate(data.remap_availability?.timed_available_at)}`],
      ["Бонусные ремапы", nullable(data.remap_availability?.bonus_remaps)],
      ["Рекомендация", escapeHtml(recommendedScenario)],
    ]),
    recommendationNote,
    boosterGuidance(data.boosters),
    h3("Сравнение вариантов"),
    table(["Сценарий", "Импланты", "Требуется", "Начнут работать", "Ремапы", "Все цели", "Первая цель", "Экономия"], scenarioRows),
    data.exact ? h3("План ремапов") + table(["Сценарий", "Перед навыком №", "Когда", "Используется", "Новые базовые атрибуты"], remapRows) : "",
    technicalDetails("Дополнительно", [
      ["Расчёт времени", data.exact ? "точный" : "диапазон из-за неизвестного окончания бустера"],
      ["Границы ремапов", "перед первым навыком и между навыками"],
      ["Расход ремапов", "обычный ремап используется раньше бонусного, если оба доступны"],
    ]),
  ].join("");
}

function renderEconomics(data) {
  if (data.blocked) {
    return [
      summaryGrid([
        ["Осталось SP", formatSP(data.remaining_sp)],
        ["Статус", `<span class="warning">Точный расчёт стоимости/времени приостановлен</span>`],
        ["Возможное время", rangeDuration(data.time?.range?.best_case_all_plans_seconds, data.time?.range?.worst_case_all_plans_seconds)],
      ]),
      `<div class="notice blocking"><strong>Нельзя корректно сравнить ISK за сэкономленный день.</strong><br>Окончание активного бустера неизвестно. Укажи оставшееся время бустера или исключи его из расчёта.</div>`,
      warningsBlock(data.warnings),
    ].join("");
  }

  const prices = {};
  (data.strategies || []).forEach((s) => (s.items || []).forEach((i) => {
    if (!(i.type_name in prices)) prices[i.type_name] = i.unit_price_isk;
  }));
  const market = data.market || {};
  const plexAvailable = data.plex?.plex_unit_price_isk != null;
  const plexLabel = plexAvailable
    ? `${formatISK(data.plex.plex_unit_price_isk)}${data.plex.source === "manual_override" ? " · вручную" : ""}`
    : `<span class="warning">Цена недоступна</span>`;
  const strategies = sortEconomicStrategies(data.strategies || [], data);

  const compactRows = strategies.map((strategy) => [
    labelStrategy(strategy, data),
    formatDuration(strategy.time?.all_plans_completion_seconds),
    formatDuration(strategy.time?.time_saved_vs_no_changes_seconds),
    costCell(strategy.cost?.total_isk),
    strategy.efficiency?.isk_per_day_saved == null ? "—" : formatISK(strategy.efficiency.isk_per_day_saved),
  ]);
  const detailRows = strategies.map((strategy) => [
    labelStrategy(strategy, data),
    formatDuration(strategy.time?.all_plans_completion_seconds),
    formatDuration(strategy.time?.time_saved_vs_no_changes_seconds),
    costCell(strategy.cost?.implants_isk),
    acceleratorCell(strategy),
    injectorCell(strategy),
    plexCostCell(strategy),
    costCell(strategy.cost?.total_isk),
    strategy.efficiency?.isk_per_day_saved == null ? "—" : formatISK(strategy.efficiency.isk_per_day_saved),
    strategyNotes(strategy),
  ]);

  const biologyLevel = Number(data.nes_accelerators?.biology_level || 0);
  const biologyRoman = biologyLevel ? romanOrNumber(biologyLevel) : "0";
  const byBonus = Number(data.nes_accelerators?.biology_implant_duration_bonus || 0);
  const nesCatalog = (data.nes_accelerators?.catalog || []).map((x) => [
    `+${x.bonus}${x.selected ? " · выбран" : ""}`,
    formatDurationDetailed(x.biology_duration_seconds),
    byBonus ? formatDurationDetailed(x.effective_duration_seconds) : "—",
    `${formatInteger(x.plex)} PLEX`,
  ]);

  return [
    summaryGrid([
      ["Рынок", "Jita 4-4"],
      ["Цены обновлены", formatDate(market.fetched_at)],
      ["PLEX", plexLabel],
    ]),
    renderLargeInjectorSelection(data),
    nesCatalog.length ? h3("Ускорители NES") + `<p class="field-hint">Фактическая длительность для текущего персонажа: Biology ${biologyRoman}${byBonus ? ` · имплант длительности +${Math.round(byBonus * 100)}%` : ""}. Одновременно действует только один ускоритель; следующий используется после окончания предыдущего.</p>` + table(["Бонус ко всем атрибутам", `С Biology ${biologyRoman}`, "Итог с имплантом длительности", "Цена"], nesCatalog) : "",
    h3("Текущие цены Jita 4-4"),
    table(["Предмет", "Цена"], Object.entries(prices).map(([name, price]) => [escapeHtml(name), price == null ? unavailable() : formatISK(price)])),
    h3("Сравнение способов ускорения"),
    table(["Способ", "До завершения", "Экономия времени", "Итого", "ISK за сэкономленный день"], compactRows),
    `<details class="technical-details economics-details"><summary>Подробности расчётов</summary>${table(["Способ", "До завершения", "Экономия времени", "Импланты", "Ускорители", "Инжекторы", "PLEX / ISK", "Итого", "ISK за сэкономленный день", "Примечания"], detailRows)}</details>`,
    summaryGrid([
      ["Самый быстрый практичный", strategyName(data.recommended_fastest)],
      ["Самый дешёвый полезный", strategyName(data.recommended_cheapest)],
      ["Лучший цена / время", strategyName(data.recommended_best_isk_per_day_saved)],
    ]),
    `<p class="field-hint">Полная мгновенная заливка всех SP инжекторами остаётся только справочным расчётом и не участвует в выборе рекомендаций. ISK за сэкономленный день: чем меньше, тем выгоднее ускорение.</p>`,
    warningsBlock([...(market.warnings || []), ...(data.warnings || [])].filter((w) => !String(w).toLowerCase().includes("sell order"))),
    technicalDetails("Дополнительно", [
      ["Источник рынка", market.source || "—"],
      ["Источник цены PLEX", data.plex?.source === "manual_override" ? "указано вручную" : (data.plex?.source || "—")],
      ["Region ID", market.region_id || "—"],
      ["Station ID", market.location_id || "—"],
    ]),
  ].join("");
}

function renderLargeInjectorSelection(data) {
  const bonus = Number(data.nes_accelerators?.selected_bonus || $("nesAcceleratorBonus")?.value || 12);
  const acceleratorMode = data.nes_accelerators?.mode || $("nesAcceleratorMode")?.value || "continuous";
  const selection = data.large_skill_injectors || {};
  const lsiMode = selection.mode || $("largeInjectorMode")?.value || "auto";
  const manualCount = Math.max(0, Number(selection.requested_count ?? $("comboLargeInjectorCount")?.value ?? 0));
  const strategies = data.strategies || [];
  let strategy = null;

  if (lsiMode === "auto") {
    strategy = strategies.find((row) => row.strategy_id === `nes_accelerator_${bonus}_large_injector_optimal`) || null;
  } else if (manualCount > 0) {
    strategy = strategies.find((row) => row.strategy_id === `nes_accelerator_${bonus}_large_injector_manual_${manualCount}`) || null;
  }
  if (!strategy) {
    strategy = strategies.find((row) => row.strategy_id === `nes_accelerator_${bonus}_${acceleratorMode}`) || null;
  }

  if (!strategy) {
    return `<div class="notice info"><strong>Large Skill Injectors:</strong> результат появится после расчёта стоимости.</div>`;
  }

  const combo = strategy.injector_combo || {};
  const count = Number(combo.count || 0);
  const spGained = Number(combo.sp_gained || 0);
  const spRemaining = combo.sp_remaining_after != null ? Number(combo.sp_remaining_after) : Number(data.remaining_sp || 0);
  const heading = lsiMode === "auto" ? "Результат автоподбора LSI" : "Выбранная вручную комбинация";
  const modeText = lsiMode === "auto"
    ? (count > 0
      ? `Автоподбор выбрал ${formatInteger(count)} Large Skill Injectors по минимальной стоимости одного сэкономленного дня.`
      : "Автоподбор выбрал 0 LSI. Это корректный результат: при текущей цене ни один LSI не снижает стоимость одного сэкономленного дня. Если важнее сильнее сократить время, выбери ручной режим и укажи количество LSI.")
    : (count > 0
      ? `Используется указанное вручную количество: ${formatInteger(count)} LSI.`
      : "Ручной режим: выбрано 0 LSI, инжекторы не используются.");
  const comboTitle = count > 0
    ? `NES +${formatInteger(bonus)} + ${formatInteger(count)} LSI`
    : `NES +${formatInteger(bonus)} · без LSI`;
  const plexQuantity = Number(strategy.plex_quantity || strategy.accelerator?.plex_quantity || 0);
  const nesCostText = plexQuantity
    ? `${formatInteger(plexQuantity)} PLEX${strategy.cost?.plex_isk == null ? "" : ` · ${formatISK(strategy.cost.plex_isk)}`}`
    : costCell(strategy.cost?.accelerators_isk);

  return `<section class="lsi-result-card">
    <div class="lsi-result-head">
      <div><span>${escapeHtml(heading)}</span><strong>${escapeHtml(comboTitle)}</strong></div>
      <span class="pill ${lsiMode === "auto" ? "ok-pill" : ""}">${lsiMode === "auto" ? "Авто" : "Вручную"}</span>
    </div>
    <p class="lsi-result-note">${escapeHtml(modeText)}</p>
    <div class="lsi-metrics">
      <div><span>Large Skill Injectors</span><strong>${formatInteger(count)}</strong></div>
      <div><span>SP от LSI</span><strong>${formatSP(spGained)}</strong></div>
      <div><span>Осталось обучить</span><strong>${formatSP(spRemaining)}</strong></div>
      <div><span>До завершения</span><strong>${formatDuration(strategy.time?.all_plans_completion_seconds)}</strong></div>
      <div><span>Экономия времени</span><strong>${formatDuration(strategy.time?.time_saved_vs_no_changes_seconds)}</strong></div>
      <div><span>Стоимость LSI</span><strong>${costCell(strategy.cost?.injectors_isk)}</strong></div>
      <div><span>NES / PLEX</span><strong>${nesCostText}</strong></div>
      <div><span>Итого</span><strong>${costCell(strategy.cost?.total_isk)}</strong></div>
      <div><span>ISK за сэкономленный день</span><strong>${strategy.efficiency?.isk_per_day_saved == null ? "—" : formatISK(strategy.efficiency.isk_per_day_saved)}</strong></div>
    </div>
  </section>`;
}

function sortEconomicStrategies(strategies, data) {
  const list = [...strategies];
  const info = (s) => s.recommendation_eligible === false ? 1 : 0;
  if (data.economic_objective === "fastest") {
    return list.sort((a, b) => info(a) - info(b) || Number(a.time?.all_plans_completion_seconds ?? Infinity) - Number(b.time?.all_plans_completion_seconds ?? Infinity));
  }
  if (data.economic_objective === "cheapest") {
    return list.sort((a, b) => info(a) - info(b) || Number(a.cost?.total_isk ?? Infinity) - Number(b.cost?.total_isk ?? Infinity));
  }
  if (data.economic_objective === "lowest_isk_per_day_saved") {
    return list.sort((a, b) => info(a) - info(b) || Number(a.efficiency?.isk_per_day_saved ?? Infinity) - Number(b.efficiency?.isk_per_day_saved ?? Infinity));
  }
  const frontier = new Set(data.pareto_frontier || []);
  return list.sort((a, b) => info(a) - info(b) || Number(!frontier.has(a.strategy_id)) - Number(!frontier.has(b.strategy_id)) || Number(a.cost?.total_isk ?? Infinity) - Number(b.cost?.total_isk ?? Infinity));
}

function acceleratorCell(strategy) {
  const a = strategy.accelerator;
  if (!a) return costCell(strategy.cost?.accelerators_isk);
  return `+${formatInteger(a.attribute_bonus)} · ${formatInteger(a.count)} шт.`;
}

function injectorCell(strategy) {
  const combo = strategy.injector_combo;
  if (combo) {
    return `${formatInteger(combo.count)} LSI · ${formatSP(combo.sp_gained)}<br><span class="field-hint">останется ${formatSP(combo.sp_remaining_after)}</span>`;
  }
  const cost = strategy.cost?.injectors_isk;
  return costCell(cost);
}

function strategyNotes(strategy) {
  const parts = [];
  if (strategy.strategy_id === "no_changes") parts.push("Справочно: базовое время без ускорений");
  if (strategy.injector_combo) {
    parts.push(`LSI: ${formatInteger(strategy.injector_combo.count)}`);
    parts.push(`SP от LSI: ${formatSP(strategy.injector_combo.sp_gained)}`);
    if (strategy.injector_combo.unallocated_sp_used) parts.push(`Свободные SP: ${formatSP(strategy.injector_combo.unallocated_sp_used)}`);
  }
  const visibleWarnings = (strategy.warnings || []).filter((w) => !String(w).toLowerCase().includes("sell order"));
  if (visibleWarnings.length) parts.push(visibleWarnings.map(localizeWarning).join("; "));
  return parts.length ? escapeHtml(parts.join(" · ")) : "—";
}

function plexCostCell(strategy) {
  const quantity = Number(strategy.plex_quantity || strategy.accelerator?.plex_quantity || 0);
  const isk = strategy.cost?.plex_isk;
  if (!quantity) return costCell(isk);
  return `${formatInteger(quantity)} PLEX${isk == null ? " · ISK недоступно" : ` · ${formatISK(isk)}`}`;
}

function renderSummary() {
  const target = $("summaryResult");
  if (!target) return;
  const schedule = state.last.schedule;
  if (!schedule) {
    target.innerHTML = `<div class="empty-state">Задай цели обучения и нажми «Рассчитать всё».</div>`;
    return;
  }
  const character = state.last.status?.character;
  const time = state.last.time;
  const economics = state.last.economics;
  const allPlans = schedule.exact
    ? maxPlanCompletion(schedule.plans)
    : rangeDuration(schedule.range?.best_case_all_plans_seconds, schedule.range?.worst_case_all_plans_seconds);
  const queue = schedule.recommended_schedule || [];
  const queuePreview = queue.slice(0, 8).map((r) => `
    <div class="queue-preview-row">
      <span class="queue-number">${r.index}</span>
      <span><strong>${escapeHtml(r.skill_name)}</strong> ${romanOrNumber(r.target_level)}</span>
      <span>${rowDuration(r)}</span>
    </div>
  `).join("");
  const goalCards = (schedule.plans || []).map((p) => `
    <div class="goal-summary-card">
      <span class="goal-priority">Приоритет ${formatNumber(p.priority)}</span>
      <strong>${escapeHtml(p.name)}</strong>
      <span>${p.completion_seconds != null ? formatDuration(p.completion_seconds) : rangeDuration(p.completion_best_case_seconds, p.completion_worst_case_seconds)}</span>
      <span class="muted">${p.completion_at ? formatDate(p.completion_at) : rangeDate(p.completion_best_case_at, p.completion_worst_case_at)}</span>
    </div>
  `).join("");

  let acceleration = `<div class="notice info">Выполни расчёт «Ускорение», чтобы получить рекомендацию по имплантам и ремапам.</div>`;
  if (time) {
    acceleration = time.exact
      ? `<div class="recommendation-card"><span>Рекомендуемый сценарий ускорения</span><strong>${escapeHtml(scenarioLabel(time.recommended?.scenario))}</strong><span>Все цели: ${formatDuration(time.recommended?.all_plans_completion_seconds)}</span></div>`
      : `<div class="notice blocking"><strong>Ремап пока не рекомендуется.</strong><br>Нужно уточнить окончание бустера или явно исключить его из расчёта.</div>`;
  }

  let cost = `<div class="notice info">Выполни расчёт «Стоимость», чтобы сравнить ISK и время.</div>`;
  if (economics) {
    if (economics.blocked) {
      cost = `<div class="notice blocking"><strong>Экономика ждёт уточнения бустера.</strong><br>Ложные 0ч 0м больше не используются.</div>`;
    } else {
      const best = strategyById(economics, economics.recommended_best_isk_per_day_saved)
        || strategyById(economics, economics.recommended_fastest);
      cost = best
        ? `<div class="recommendation-card"><span>Рекомендуемый вариант цена / время</span><strong>${escapeHtml(strategyName(best.strategy_id))}</strong><span>${formatISK(best.cost?.total_isk)} · экономия ${formatDuration(best.time?.time_saved_vs_no_changes_seconds)}</span></div>`
        : `<div class="notice info">Нет доступного точного платного варианта для автоматической рекомендации.</div>`;
    }
  }

  target.innerHTML = `
    <div class="hero-summary">
      <div><span>Персонаж</span><strong>${escapeHtml(character?.character_name || "Текущий персонаж")}</strong><small>${character ? formatSP(character.total_sp) : ""}</small></div>
      <div><span>Осталось до целей</span><strong>${formatSP(schedule.summary?.remaining_sp)}</strong><small>${formatInteger(schedule.summary?.unique_tasks)} этапов</small></div>
      <div><span>До завершения всех целей</span><strong>${allPlans}</strong><small>${schedule.exact ? "точный расчёт" : "диапазон"}</small></div>
    </div>
    ${boosterGuidance(schedule.boosters)}
    <h3>Когда будут готовы цели</h3>
    <div class="goal-summary-grid">${goalCards || `<p class="muted">Цели уже выполнены.</p>`}</div>
    <h3>Что качать первым</h3>
    <div class="queue-preview">${queuePreview || `<p class="muted">Все выбранные навыки уже изучены.</p>`}</div>
    ${queue.length ? `<p class="field-hint">Кнопка «Скопировать для EVE» копирует эту очередь в формат, который можно сразу вставить через «Импортировать навыки из буфера обмена» в EVE Online.</p>` : ""}
    ${queue.length > 8 ? `<p class="field-hint">Показаны первые 8 из ${queue.length}. Полная очередь — во вкладке «Рекомендованная очередь».</p>` : ""}
    <div class="grid two summary-bottom-grid">
      <div><h3>Ускорение</h3>${acceleration}</div>
      <div><h3>Стоимость</h3>${cost}</div>
    </div>
  `;
}

function scheduleTable(rows) {
  return table(["№", "Навык", "Уровень", "SP", "Время обучения", "Начало", "Окончание", "Атрибуты", "Требуемые навыки", "Завершает цели"], rows.map((r) => [
    r.index,
    escapeHtml(r.skill_name),
    romanOrNumber(r.target_level),
    formatSP(r.remaining_sp),
    rowDuration(r),
    rowStart(r),
    rowFinish(r),
    `${attributeName(r.primary_attribute)} / ${attributeName(r.secondary_attribute)}`,
    prerequisiteText(r),
    (r.completes_plans || []).length ? escapeHtml((r.completes_plans || []).join(", ")) : "—",
  ]));
}

function prerequisiteText(row) {
  if (Array.isArray(row.prerequisites) && row.prerequisites.length) {
    return row.prerequisites.map((p) => `${escapeHtml(p.skill_name)} ${romanOrNumber(p.level)}`).join("<br>");
  }
  return "—";
}

function rowDuration(row) {
  if (row.duration_seconds != null) return formatDuration(row.duration_seconds);
  return rangeDuration(row.duration_best_case_seconds, row.duration_worst_case_seconds);
}

function rowStart(row) {
  if (row.starts_at) return formatDate(row.starts_at);
  return rangeDate(row.starts_at_best_case, row.starts_at_worst_case);
}

function rowFinish(row) {
  if (row.finishes_at) return formatDate(row.finishes_at);
  return rangeDate(row.finishes_at_best_case, row.finishes_at_worst_case);
}

function table(headers, rows) {
  if (!rows || !rows.length) return `<p class="muted table-empty">Нет данных.</p>`;
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell, index) => `<td data-label="${escapeAttr(headers[index] || "")}">${cell == null || cell === "" ? "—" : cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function summaryGrid(items) {
  return `<div class="summary-grid">${items.map(([k, v]) => `<div class="summary-card"><span>${escapeHtml(k)}</span><strong>${v == null || v === "" ? "Не рассчитано" : v}</strong></div>`).join("")}</div>`;
}

function kvGrid(items) {
  return items.map(([k, v]) => `<div class="kv"><span>${escapeHtml(k)}</span><strong>${v == null || v === "" ? "Не рассчитано" : v}</strong></div>`).join("");
}

function h3(text) {
  return `<h3>${escapeHtml(text)}</h3>`;
}

function technicalDetails(title, items) {
  return `<details class="technical-details"><summary>${escapeHtml(title)}</summary>${kvGrid(items)}</details>`;
}

function boosterGuidance(boosters) {
  if (!boosters) return "";
  if (boosters.ignored) {
    return `<div class="notice info"><strong>Бустер исключён из расчёта по твоему выбору.</strong></div>`;
  }
  const importedBonus = boosters.imported_attribute_bonus || boosters.attribute_bonus || {};
  const hasBonus = Object.values(importedBonus).some((v) => Number(v) > 0);
  if (!hasBonus) return "";
  if (boosters.expires_at) {
    return `<div class="notice ok-notice"><strong>Бустер учтён до ${formatDate(boosters.expires_at)}.</strong> Источник: ${escapeHtml(boosterSourceLabels[boosters.expiry_source] || "определено программой")}.</div>`;
  }
  return `<div class="notice warning-notice"><strong>Окончание бустера неизвестно.</strong> Показан диапазон времени. Точные ремапы и экономика не выдаются, пока не выбрано действие.<span class="notice-actions"><button type="button" data-open-booster>Указать вручную</button><button type="button" data-ignore-booster>Игнорировать бустер</button></span></div>`;
}

function warningsBlock(warnings) {
  const list = [...new Set((warnings || []).filter(Boolean).map(localizeWarning))];
  if (!list.length) return "";
  return `<div class="notice warning-notice">${list.map((w) => escapeHtml(w)).join("<br>")}</div>`;
}

function warningText(warnings) {
  const list = [...new Set((warnings || []).filter(Boolean).map(localizeWarning))];
  return list.length ? list.map((w) => `<span class="pill warning">${escapeHtml(w)}</span>`).join(" ") : "—";
}

function localizeWarning(warning) {
  const w = String(warning || "");
  const exact = {
    "Active booster expiration could not be uniquely inferred from EVEMon queue timestamps.": "Не удалось однозначно определить окончание бустера по очереди EVEMon.",
    "EVEMon queue timestamps are inconsistent with a single booster expiration.": "Время в очереди EVEMon не позволяет надёжно определить единое окончание бустера.",
    "Active booster bonus is known, but EVEMon queue data is insufficient to infer expiration.": "Бонус бустера известен, но в очереди EVEMon недостаточно данных для определения его окончания.",
    "Active booster expiration could not be solved from EVEMon queue timestamps.": "Не удалось вычислить окончание бустера по очереди EVEMon.",
    "Inferred booster expiration is not in the future.": "Вычисленное окончание бустера оказалось не в будущем и не используется.",
    "PLEX Jita 4-4 price unavailable, so PLEX offers cannot be converted": "Цена PLEX на Jita 4-4 недоступна — пересчёт предложений PLEX в ISK не выполняется.",
    "no active Jita 4-4 sell order": "Нет активного sell order на Jita 4-4.",
    "completion_time_is_estimate": "Время этой стратегии является оценкой и не участвует в автоматическом выборе лучшего варианта.",
    "current implants are already owned; incremental cost is 0 ISK": "Текущие импланты уже принадлежат персонажу — дополнительная стоимость 0 ISK.",
    "no live reliable NES offer source configured; old pack prices are not hardcoded": "Надёжный live-источник предложений NES не настроен; старые цены не подставляются.",
  };
  if (exact[w]) return exact[w];
  let match = w.match(/^injectors=(\d+)$/);
  if (match) return `Нужно инжекторов: ${formatInteger(match[1])}`;
  match = w.match(/^sp_gained=(\d+)$/);
  if (match) return `Получено SP: ${formatInteger(match[1])}`;
  match = w.match(/^excess_sp=(\d+)$/);
  if (match) return `Лишних SP: ${formatInteger(match[1])}`;
  match = w.match(/^unallocated_sp_used=(\d+)$/);
  if (match) return `Использовано свободных SP: ${formatInteger(match[1])}`;
  if (w.includes("has no Jita 4-4 sell order")) return w.replace("has no Jita 4-4 sell order", "— нет активного sell order на Jita 4-4");
  if (w.includes("Booster expiration is unknown")) return "Окончание бустера неизвестно. Укажи время вручную или исключи бустер из расчёта.";
  if (w.includes("accelerator pricing requires")) return "Для ускорителя нужен конкретный предмет/источник цены; выдуманная цена не используется.";
  if (w.includes("future accelerator model")) return "Рыночные ускорители пока доступны только после указания конкретного предмета/источника.";
  if (w.includes("NES/pack offer prices")) return "Цены NES/pack не берутся из ESI и не подставляются вручную.";
  return w;
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    throw new Error(`Не удалось связаться с локальным сервером: ${error.message}`);
  }
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Локальный сервер вернул некорректный ответ (${response.status}).`);
  }
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
    throw new Error(localizeError(detail, response.status));
  }
  return data;
}

function localizeError(detail, status) {
  const mappings = [
    ["No imported character", "Персонаж не импортирован. Сначала выбери файл EVEMon и импортируй персонажа."],
    ["SDE not loaded", "База данных EVE (SDE) не загружена. Нажми «Обновить SDE»."],
    ["booster_expires_at_override must be timezone-aware", "Некорректная дата окончания бустера."],
    ["Choose either booster_expires_at_override or ignore_active_booster", "Нельзя одновременно указывать окончание бустера и игнорировать его."],
    ["booster_expires_at_override was supplied, but booster strength is unknown", "Время бустера указано, но сила бустера неизвестна. Укажи силу вручную."],
    ["booster_expires_at_override must be in the future", "Указанное время окончания бустера уже прошло."],
    ["Training time calculation returned zero while remaining SP is positive", "Расчёт времени вернул невозможный ноль при ненулевых SP. Экономический расчёт остановлен, чтобы не показать ложный результат."],
    ["Unknown EVE skill", "Не удалось распознать один из навыков. Вставляй английское имя навыка с уровнем или строку EVE вида <localized hint=\"English Skill\">Русское название*</localized> 4."],
  ];
  const found = mappings.find(([needle]) => String(detail).includes(needle));
  return found ? found[1] : `Ошибка ${status}: ${detail}`;
}

async function runButton(buttonOrId, messageId, fn) {
  const button = typeof buttonOrId === "string" ? $(buttonOrId) : buttonOrId;
  const original = button?.textContent || "";
  const skipProgress = /копировать|скачать/i.test(original);
  if (button) {
    button.disabled = true;
    button.textContent = "Выполняется…";
  }
  if (!skipProgress) startOperationProgress(original || "Операция выполняется");
  try {
    return await fn();
  } catch (error) {
    if (messageId && $(messageId)) {
      $(messageId).innerHTML = `<div class="notice blocking">${escapeHtml(error.message)}</div>`;
    } else {
      showTopError(error);
    }
    return null;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = original;
    }
    if (!skipProgress) stopOperationProgress();
  }
}

function startOperationProgress(label) {
  const box = $("operationProgress");
  if (!box) return;
  if (state.operation.timer) clearInterval(state.operation.timer);
  state.operation.startedAt = Date.now();
  box.hidden = false;
  $("operationProgressLabel").textContent = label || "Выполняется…";
  $("operationProgressDetail").textContent = "Программа работает. Оценка оставшегося времени недоступна.";
  const bar = $("operationProgressBar");
  bar.className = "progress-bar indeterminate";
  bar.style.width = "";
  const tick = () => {
    const elapsed = Math.max(0, Math.floor((Date.now() - state.operation.startedAt) / 1000));
    $("operationProgressElapsed").textContent = `Прошло: ${elapsed}с`;
  };
  tick();
  state.operation.timer = setInterval(tick, 1000);
}

function setOperationProgress(label, detail, percent = null) {
  const box = $("operationProgress");
  if (!box) return;
  box.hidden = false;
  if (!state.operation.startedAt) state.operation.startedAt = Date.now();
  $("operationProgressLabel").textContent = label || "Выполняется…";
  $("operationProgressDetail").textContent = detail || "Программа работает.";
  const bar = $("operationProgressBar");
  if (percent == null) {
    bar.className = "progress-bar indeterminate";
    bar.style.width = "";
  } else {
    bar.className = "progress-bar";
    bar.style.width = `${Math.max(0, Math.min(100, Number(percent)))}%`;
  }
}

function stopOperationProgress() {
  if (state.operation.timer) clearInterval(state.operation.timer);
  state.operation.timer = null;
  state.operation.startedAt = null;
  const box = $("operationProgress");
  if (box) box.hidden = true;
}

function markEconomicsDirty() {
  // Настройки экономики никогда не запускают расчёт сами. Пользователь сначала
  // выбирает параметры, затем явно запускает «Рассчитать всё» или
  // «Рассчитать стоимость». Инвалидируем уже запущенный запрос, чтобы его
  // старый ответ не перезаписал результат после изменения настроек.
  state.economicsRequestSeq += 1;
  setMessage(
    "economicsSettingsMessage",
    "Настройки изменены. Нажми «Рассчитать всё» для полного пересчёта или «Рассчитать стоимость» только для этой вкладки.",
    "info",
  );
}


function showTopError(error) {
  setMessage("globalMessage", error?.message || String(error), "error");
}

function setMessage(id, text, cls = "") {
  const el = $(id);
  if (!el) return;
  el.className = `message ${cls}`;
  el.textContent = text;
}

function setRaw(kind, data) {
  state.last.kind = kind;
  state.last.current = data;
  if ($("rawJson")) $("rawJson").textContent = JSON.stringify(data, null, 2);
}

async function copyEvePlan() {
  let schedule = state.last.schedule;
  if (!schedule) {
    schedule = await runSchedule();
  }
  const rows = schedule?.recommended_schedule || [];
  if (!rows.length) {
    throw new Error("В рекомендованной очереди нет навыков для копирования.");
  }
  const missing = rows.filter((row) => !row.eve_clipboard_line);
  if (missing.length) {
    throw new Error("Для части навыков не удалось подготовить формат EVE. Обнови SDE и пересчитай очередь.");
  }
  const text = rows.map((row) => row.eve_clipboard_line).join("\r\n");
  await writeClipboardText(text);
  const message = `Скопировано для EVE: ${rows.length} уровней навыков. В EVE выбери «Импортировать навыки из буфера обмена».`;
  setMessage("scheduleCopyMessage", message, "ok");
  setMessage("summaryCopyMessage", message, "ok");
  return schedule;
}

async function writeClipboardText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) {
    throw new Error("Браузер не разрешил доступ к буферу обмена.");
  }
}

function copyJson() {
  if (!navigator.clipboard?.writeText) {
    setMessage("rawMessage", "Буфер обмена недоступен в этом браузере.", "error");
    return;
  }
  navigator.clipboard.writeText($("rawJson").textContent)
    .then(() => setMessage("rawMessage", "JSON скопирован в буфер обмена.", "ok"))
    .catch(() => setMessage("rawMessage", "Нет разрешения на буфер обмена. Используй «Скачать JSON».", "error"));
}

function downloadJson() {
  downloadBlob("eve-skill-optimizer-result.json", $("rawJson").textContent, "application/json");
}

function downloadScheduleCsv() {
  const rows = state.last.schedule?.recommended_schedule || state.last.time?.no_changes?.schedule || [];
  downloadBlob("eve-schedule.csv", rowsToCsv(rows), "text/csv");
}

function downloadEconomicsCsv() {
  const rows = (state.last.economics?.strategies || []).map((s) => ({
    strategy_id: s.strategy_id,
    strategy_name_ru: strategyName(s.strategy_id),
    completion_days: s.time?.all_plans_completion_days,
    days_saved: s.time?.time_saved_vs_no_changes_days,
    total_isk: s.cost?.total_isk,
    isk_per_day_saved: s.efficiency?.isk_per_day_saved,
    exact: s.exact,
    warnings: (s.warnings || []).join("; "),
  }));
  downloadBlob("eve-economics-strategies.csv", rowsToCsv(rows), "text/csv");
}

function rowsToCsv(rows) {
  if (!rows.length) return "";
  const keys = Object.keys(rows[0]);
  return [keys.join(","), ...rows.map((row) => keys.map((key) => csvCell(row[key])).join(","))].join("\n");
}

function csvCell(value) {
  const text = Array.isArray(value) ? value.join("; ") : value == null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadBlob(filename, content, type) {
  const blob = new Blob([content], { type });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function loadUiState() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(storageKey) || localStorage.getItem("eveSkillOptimizer.ui.v1") || "{}");
  } catch {
    saved = {};
  }
  state.plans = saved.plans || [
    { name: "Planetary", priority: 4, text: "Planetology V\nAdvanced Planetology IV" },
    { name: "Jump Drive", priority: 2, text: "Jump Drive Operation V\nJump Drive Calibration IV" },
  ];
  $("evemonPath").value = saved.evemonPath || localStorage.getItem("eveSkillOptimizer.evemonPath") || "";
  $("objective").value = saved.objective || "weighted_completion_time";
  $("respectQueue").checked = !!saved.respectQueue;
  $("useImported").checked = saved.useImported !== false;
  $("economicObjective").value = saved.economicObjective || "pareto";
  ["includeLargeInjectors", "includeSmallInjectors", "useUnallocatedSp", "includeNesAccelerators"].forEach((id) => { if ($(id)) $(id).checked = saved[id] !== false; });
  const oldScenarios = saved.implantScenarios || [];
  $("implantScenario").value = saved.implantScenario || oldScenarios[0] || "current";
  $("nesAcceleratorBonus").value = saved.nesAcceleratorBonus || "12";
  $("nesAcceleratorMode").value = saved.nesAcceleratorMode || "continuous";
  $("nesAcceleratorCount").value = saved.nesAcceleratorCount || 1;
  $("largeInjectorMode").value = saved.largeInjectorMode || (saved.optimizeLargeInjectors === false ? "manual" : "auto");
  const savedUiSchemaVersion = Number(saved.uiSchemaVersion || 0);
  $("comboLargeInjectorCount").value = savedUiSchemaVersion >= uiSchemaVersion ? (saved.comboLargeInjectorCount || 0) : 0;
  $("manualPlexPrice").value = saved.manualPlexPrice || "";
  $("boosterExpiryOverride").value = saved.boosterExpiryOverride || "";
  $("boosterBonusOverride").value = saved.boosterBonusOverride || "";
  $("boosterRemainingHours").value = saved.boosterRemainingHours || "";
  $("boosterRemainingMinutes").value = saved.boosterRemainingMinutes || "";
  $("boosterRemainingSeconds").value = saved.boosterRemainingSeconds || "";
  $("ignoreBooster").checked = !!saved.ignoreBooster;
  if (saved.selectedTab && document.querySelector(`.tab[data-tab="${saved.selectedTab}"]`)) switchTab(saved.selectedTab);
}

function saveUiState() {
  if (!$("objective")) return;
  const activeTab = document.querySelector(".tab.active")?.dataset.tab || "character";
  localStorage.setItem(storageKey, JSON.stringify({
    uiSchemaVersion,
    plans: state.plans,
    evemonPath: $("evemonPath").value,
    objective: $("objective").value,
    respectQueue: $("respectQueue").checked,
    useImported: $("useImported").checked,
    economicObjective: $("economicObjective").value,
    includeLargeInjectors: $("includeLargeInjectors").checked,
    includeSmallInjectors: $("includeSmallInjectors").checked,
    includeNesAccelerators: $("includeNesAccelerators").checked,
    nesAcceleratorBonus: $("nesAcceleratorBonus").value,
    nesAcceleratorMode: $("nesAcceleratorMode").value,
    nesAcceleratorCount: $("nesAcceleratorCount").value,
    largeInjectorMode: $("largeInjectorMode").value,
    comboLargeInjectorCount: $("comboLargeInjectorCount").value,
    manualPlexPrice: $("manualPlexPrice").value,
    useUnallocatedSp: $("useUnallocatedSp").checked,
    implantScenario: $("implantScenario").value,
    boosterExpiryOverride: $("boosterExpiryOverride").value,
    boosterBonusOverride: $("boosterBonusOverride").value,
    boosterRemainingHours: $("boosterRemainingHours").value,
    boosterRemainingMinutes: $("boosterRemainingMinutes").value,
    boosterRemainingSeconds: $("boosterRemainingSeconds").value,
    ignoreBooster: $("ignoreBooster").checked,
    selectedTab: activeTab,
  }));
}

function formatISK(value) {
  if (value == null || Number.isNaN(Number(value))) return "Недоступно";
  const n = Number(value);
  if (Math.abs(n) >= 1_000_000_000) return `${trimZeros((n / 1_000_000_000).toFixed(2))} млрд ISK`;
  if (Math.abs(n) >= 1_000_000) return `${trimZeros((n / 1_000_000).toFixed(1))} млн ISK`;
  return `${Math.round(n).toLocaleString("ru-RU")} ISK`;
}

function formatSP(value) {
  const n = Number(value || 0);
  if (Math.abs(n) >= 1_000_000) return `${trimZeros((n / 1_000_000).toFixed(2))} млн SP`;
  return `${Math.round(n).toLocaleString("ru-RU")} SP`;
}

function formatInteger(value) {
  if (value == null || value === "") return "—";
  return Math.round(Number(value)).toLocaleString("ru-RU");
}

function formatTimeDifferenceFromOptimized(otherSeconds, recommendedSeconds) {
  if (otherSeconds == null || recommendedSeconds == null) return "—";
  const diff = Number(otherSeconds) - Number(recommendedSeconds);
  if (!Number.isFinite(diff)) return "—";
  if (Math.abs(diff) <= 0.5) return "0с";
  return `${diff > 0 ? "+" : "−"}${formatDurationDetailed(Math.abs(diff))}`;
}

function formatDurationDetailed(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return "Не рассчитано";
  let s = Math.max(0, Math.round(Number(seconds)));
  const d = Math.floor(s / 86400); s %= 86400;
  const h = Math.floor(s / 3600); s %= 3600;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  const parts = [];
  if (d) parts.push(`${d}д`);
  if (h || d) parts.push(`${h}ч`);
  if (m || h || d) parts.push(`${m}м`);
  if (sec && !d) parts.push(`${sec}с`);
  return parts.join(" ") || "0с";
}

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return "Не рассчитано";
  const n = Number(seconds);
  const sign = n < 0 ? "−" : "";
  let s = Math.abs(Math.round(n));
  const d = Math.floor(s / 86400); s %= 86400;
  const h = Math.floor(s / 3600); s %= 3600;
  const m = Math.floor(s / 60);
  if (d > 0) return `${sign}${d}д ${h}ч ${m}м`;
  if (h > 0) return `${sign}${h}ч ${m}м`;
  return `${sign}${m}м`;
}

function rangeDuration(best, worst) {
  if (best == null && worst == null) return "Не рассчитано";
  if (best == null) return `до ${formatDuration(worst)}`;
  if (worst == null) return `от ${formatDuration(best)}`;
  const b = Number(best);
  const w = Number(worst);
  if (Math.abs(b - w) < 1) return formatDuration(b);
  return `${formatDuration(Math.min(b, w))} – ${formatDuration(Math.max(b, w))}`;
}

function formatDate(value) {
  if (!value) return "Не рассчитано";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "Не рассчитано";
  return `<span title="${escapeAttr(value)}">${d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })}</span>`;
}

function rangeDate(best, worst) {
  if (!best && !worst) return "Не рассчитано";
  if (best && worst && best === worst) return formatDate(best);
  return `${best ? formatDate(best) : "?"} – ${worst ? formatDate(worst) : "?"}`;
}

function localDateTimeToIso(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function formatAttributes(attrs = {}) {
  return `INT ${attrs.intelligence ?? 0} · MEM ${attrs.memory ?? 0} · PER ${attrs.perception ?? 0} · WIL ${attrs.willpower ?? 0} · CHA ${attrs.charisma ?? 0}`;
}

function attributeName(value) {
  return attributeLabels[String(value || "").toLowerCase()] || escapeHtml(value || "—");
}

function formatNumber(value) {
  return value == null || Number.isNaN(Number(value)) ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 });
}

function durationCell(obj) {
  if (!obj) return "Не рассчитано";
  if (obj.exact === false) return rangeDuration(obj.best_case_seconds, obj.worst_case_seconds);
  return formatDuration(obj.seconds);
}

function firstPlanDuration(scenario) {
  const times = (scenario.plans || []).map((p) => p.completion_seconds).filter((v) => v != null);
  return times.length ? formatDuration(Math.min(...times)) : "Не рассчитано";
}

function scheduleSeconds(rows = []) {
  const values = rows.map((row) => row.duration_seconds).filter((v) => v != null);
  if (!values.length && rows.length) return null;
  return values.reduce((sum, value) => sum + Number(value), 0);
}

function maxPlanCompletion(plans = []) {
  const values = plans.map((p) => p.completion_seconds).filter((v) => v != null);
  return values.length ? formatDuration(Math.max(...values)) : "0м";
}

function scenarioLabel(value) {
  const labels = { current: "Текущие импланты", none: "Без имплантов", "+3": "Импланты +3", "+4": "Импланты +4", "+5": "Импланты +5" };
  return labels[value] || (value ? String(value) : "Не рассчитано");
}

function remapKind(value) {
  if (value === "timed") return "обычный ремап";
  if (value === "bonus") return "бонусный ремап";
  return value || "—";
}

function optimizerAlgorithmLabel(value) {
  if (value === "exact") return "точный перебор допустимых вариантов";
  if (value === "beam_search") return "расширенный поиск";
  if (value === "range") return "расчёт диапазона";
  return value || "—";
}

function optimizerOptimalityLabel(value) {
  if (value === "proven") return "оптимальный результат математически доказан";
  if (value === "heuristic") return "найден лучший из рассмотренных вариантов; абсолютный оптимум не гарантирован";
  if (value === "not_proven_due_unknown_booster_expiry") return "точный оптимум не определяется до уточнения окончания бустера";
  return value || "—";
}

function strategyName(id) {
  if (!id) return "Не рассчитано";
  const value = String(id);
  const optimalCombo = value.match(/^nes_accelerator_(\d+)_large_injector_optimal$/);
  if (optimalCombo) return `NES +${optimalCombo[1]} + оптимальное количество Large Skill Injectors`;
  const manualCombo = value.match(/^nes_accelerator_(\d+)_large_injector_manual_(\d+)$/);
  if (manualCombo) return `NES +${manualCombo[1]} + ${manualCombo[2]} Large Skill Injectors`;
  const candidateCombo = value.match(/^nes_accelerator_(\d+)_large_injector_candidate_(\d+)$/);
  if (candidateCombo) return `NES +${candidateCombo[1]} + ${candidateCombo[2]} Large Skill Injectors`;
  const nes = value.match(/^nes_accelerator_(\d+)_(continuous|count)$/);
  if (nes) return `Ускоритель NES +${nes[1]}${nes[2] === "continuous" ? " — без перерывов" : " — ограниченное количество"}`;
  return strategyLabels[id] || value.replaceAll("_", " ");
}

function labelStrategy(strategy, data) {
  const marks = [];
  if (strategy.informational || strategy.recommendation_eligible === false) marks.push("Справочно");
  if (strategy.strategy_id === data.recommended_fastest) marks.push("Самый быстрый практичный");
  if (strategy.strategy_id === data.recommended_cheapest) marks.push("Самый дешёвый полезный");
  if (strategy.strategy_id === data.recommended_best_isk_per_day_saved) marks.push("Лучший ISK/день");
  if ((data.pareto_frontier || []).includes(strategy.strategy_id)) marks.push("Лучший цена/время");
  if (!strategy.exact) marks.push("Оценка");
  return `<strong>${escapeHtml(strategyName(strategy.strategy_id))}</strong>${marks.length ? `<br>${marks.map((m) => `<span class="pill">${escapeHtml(m)}</span>`).join(" ")}` : ""}`;
}

function strategyById(data, id) {
  return (data?.strategies || []).find((s) => s.strategy_id === id) || null;
}

function costCell(value) {
  return value == null ? unavailable() : formatISK(value);
}

function unavailable() {
  return `<span class="warning">Недоступно</span>`;
}

function nullable(value) {
  return value == null ? "—" : value;
}

function romanOrNumber(value) {
  const n = Number(value);
  return ({ 1: "I", 2: "II", 3: "III", 4: "IV", 5: "V" })[n] || escapeHtml(value);
}

function trimZeros(value) {
  return String(value).replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}
