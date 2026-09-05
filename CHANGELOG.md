# Changelog

## 0.9.10

- Economics settings are applied only after an explicit `Рассчитать всё` / `Рассчитать стоимость` action.
- Changing economics settings invalidates stale in-flight results.
- Jita market price of an already-owned implant is displayed normally while incremental purchase cost remains 0 ISK.
- Manual/auto LSI, NES, PLEX and implant selection remain part of the economic optimizer.

## 0.9.9

- Fixed stale economics responses overwriting the latest manual Large Skill Injector count.
- Backend explicitly returns LSI mode and requested manual count.
- Auto-selection of 0 LSI is explained as a valid economic result.

## 0.9.8

- Improved manual/auto LSI UX and removed misleading duplicate NES presentation.

## 0.9.7

- Added explicit `Автоподбор / Вручную` Large Skill Injector modes and a combined NES + LSI result card.

## 0.9.6

- Improved NES quantity UX and schedule baseline comparison.

## 0.9.5

- Added practical economic recommendations, sequential NES accelerator modelling, Biology/BY duration handling, NES + LSI combinations and progress indicators.

## 0.9.2

- Added localized EVE/EVEMon skill-plan parsing and friendly unknown-skill errors.

## 0.9.1

- Added manual booster remaining time and strength override.

## 0.9.0

- Major Russian UI/UX revision; fixed false `0h 0m`/`n/a` timing propagation, prerequisite names, booster uncertainty handling and final recommendation screen.
