// The viewer's own texts in three languages. Model data (part names, the --world-label) is not translated.
// Language of a page: ?lang= -> the visitor's choice (localStorage "eng.lang") -> the project's (public/locale.json, written by the
// router from .engineer/locale.json; none = ru, like the router). Not the browser's: a Russian project stays Russian for a client
// whose browser speaks English. The page sets cookie eng_lang so the router answers in the same language.
export const LANGS = ["ru", "en", "pl"];
// The visitor's pick is remembered per project (one browser often opens several clients' projects).
export const LANG_KEY = "eng.lang." + ((typeof location !== "undefined" && location.pathname.split("/")[1]) || "");
const ok = (x) => { const m = /^(ru|en|pl)(-|_|$)/i.exec(String(x || "").trim()); return m ? m[1].toLowerCase() : ""; };

export function pickLang({ query = "", stored = "", project = "" } = {}) {
  return ok(query) || ok(stored) || ok(project) || "ru";
}

export async function resolveLang(base) {
  let stored = "", project = "", known = false;
  try { stored = localStorage.getItem(LANG_KEY) || ""; } catch {}
  const query = new URLSearchParams(location.search).get("lang") || "";
  if (!ok(query) && !ok(stored)) {
    const ac = new AbortController(), t = setTimeout(() => ac.abort(), 1500);   // the page must not hang on it (no AbortSignal.timeout: older Safari)
    try {
      const r = await fetch(new URL("locale.json", base), { cache: "no-store", signal: ac.signal });
      known = r.status === 200 || r.status === 204;   // 204: the project has no language = ru
      if (r.status === 200) project = (await r.json()).lang || "";
    } catch {} finally { clearTimeout(t); }
    if (!known) project = (/(?:^|;\s*)eng_lang=(\w+)/.exec(document.cookie) || [])[1] || "";   // no answer: the language this visitor had
  }
  const lang = pickLang({ query, stored, project });
  if (ok(query)) { try { localStorage.setItem(LANG_KEY, lang); } catch {} }
  document.documentElement.lang = lang;
  if (ok(query) || ok(stored) || known) document.cookie = `eng_lang=${lang}; path=/; max-age=31536000; samesite=lax`;
  return lang;
}

// Picking a language in the switcher: remember it, drop a ?lang= that would override it, reload (the hash = the view, kept).
export function switchLang(lang) {
  try { localStorage.setItem(LANG_KEY, lang); } catch {}
  document.cookie = `eng_lang=${lang}; path=/; max-age=31536000; samesite=lax`;
  const u = new URL(location.href);
  if (!u.searchParams.has("lang")) return location.reload();   // a URL that differs only after "#" would not load again
  u.searchParams.delete("lang");
  location.replace(u.toString());
}

const ru = {
  reset: "Сбросить вид", showAll: "Показать все (скрыто: {n})", hide: "Скрыть деталь", unhide: "Вернуть деталь",
  hideAsm: "Скрыть узел", unhideAsm: "Вернуть узел",
  hideStem: "Скрыть все такие ({n})", unhideStem: "Вернуть все такие ({n})", center: "Центр вращения сюда",
  only: "Показать только это", close: "Закрыть", fold: "Свернуть / развернуть", search: "Поиск: название или номер чертежа",
  step: "Скачать STEP", stl: "Скачать STL", loading: "Загрузка модели… {pct}% ({a} из {b} МБ)", parsing: "Разбор модели…",
  ready: "{m} деталей в сборке · {p} различных · {t} млн треугольников", failed: "Не удалось загрузить: {e}",
  drawing: "Чертёж", kind: "Тип", module: "Модуль", count: "В машине", pcs: "{n} шт.", size: "Габарит X×Y×Z", mm: "мм",
  volume: "Объём", mass: "Масса (оценка)", massNote: "≈ {m} — оценка (объём × 7,85 г/см³, материал по названию)", massNoteDensity: "≈ {m} (оценка: объём × плотность материала)", thickness: "Толщина металла", cm3: "см³", kg: "кг", g: "г",
  parts: "Деталей в узле", asm: "Узел", nothing: "Ничего не найдено", more: "…и ещё {n}", distinct: "{n} различных",
  kinds: { made: "изготавливаемая", standard: "стандартная", purchased: "покупная" },
  colorBy: "Цвет:", byCad: "из CAD", byMaterial: "материал", byKind: "тип", cadNote: "Цвета заданы в CAD-модели (STEP).", material: "Материал",
  view: "Вид:", edges: "Контуры", ao: "Тени в щелях", hc: "Высокий контраст", matNote: " (по названию, не из CAD)", edgesLoading: "Контуры… {pct}%",
  views: { front: "Спереди", back: "Сзади", left: "Слева", right: "Справа", top: "Сверху", bottom: "Снизу", iso: "Изометрия" },
  grid: { on: "Сетка", step: "Шаг сетки:", auto: "авто", cell: "{n} мм", size: "Габариты: {s}" },
  meas: { on: "Линейка", hint: "кликните две точки на модели", second: "вторая точка…", clear: "Сбросить",
          out: "{d} мм", split: "по горизонтали {h} · по высоте {v} мм" },
  aoOff: "Тени в щелях выключены автоматически: слабый компьютер или телефон. Кнопка включает их снова.",
  materials: { steel: "сталь, листовой металл", printed: "3D-печать (пластик)", rubber: "резина, ремни", fastener: "крепёж", bearing: "подшипники",
    motor: "двигатели", electronics: "электроника", pneumatic: "пневматика", glass: "стекло", other: "прочее" },
  legendKinds: { made: "изготавливаемая", standard: "стандартная", purchased: "покупная" },
  hintDesk: "клик — карточка · двойной клик — центр вращения · тащить — вращать · правая кнопка или Shift — сдвиг · колесо — масштаб",
  hintTouch: "тап — карточка · двойной тап — центр вращения · один палец — вращать · два пальца — сдвиг и масштаб",
  decimal: ",",
  sec: { view: "Вид", hw: "Платы на столе", dl: "Скачать файлы", parts: "Детали" },
  lang: "Язык",
  banner: "Модель обновляется…",
  clash: { head: "⚠ Детали пересекаются: {n}", tip: "Проверка модели нашла детали, которые занимают одно место. ", fixing: "ИИнженер это исправляет.",
           byCad: "Модель перенесена из вашего CAD", built: "Машина уже собрана",
           withYou: ": ИИнженер разбирает их с вами и меняет модель только с вашего согласия.", mm3: "мм³", s: "с" },
  apk: { button: "Скачать приложение (APK)", tip: "Приложение для Android, версия {v}", ver: "Android, версия {v}" },
  motion: { world: "Показывать изделие", noCad: "нет в CAD", source: "Источник", unresolved: "Движение CAD: не найдено узлов — {n} (см. консоль)", s: "с" },
  device: "Устройство", keySpace: "Пробел",
  wires: { show: "Провода ({n})", tip: "Провода внутри машины по их трассам, цвет — по цепи (длины отрезов — в списке покупок)" },
  sim: {
    pause: "Пауза", resume: "Продолжить", reset: "Сброс", max: "макс", clockTip: "Время машины (виртуальное)",
    ach: "факт ×{x}", lag: "×{x} — не успевает", maxAch: "макс ≈×{x}", paused: "на паузе", crashed: "сбой прошивки", boards: "Платы", machine: "Симуляция", flashReal: "Прошить на плату",
    bstatus: { run: "работает", idle: "ожидание", crash: "сбой", paused: "пауза" },
    mcu: "МК", fw: "Прошивка", fwNote: "Смена прошивки перезагружает плату и сбрасывает машину.", status: "Статус", role: "Роль", cad: "Деталь в CAD",
    noCad: "не сопоставлена (по CAD нельзя определить)", show3d: "Показать в 3D", pins: "Пины: сеть / устройство, уровень", hideNums: "скрыть строки с весами",
    i2c: "I²C: обмен этой платы", noI2c: "пока нет обмена", reloading: "перезагрузка…"
  },
  cycle: { play: "Пуск", pause: "Пауза", t: "Время цикла, с", tip: "Цикл машины (по расписанию из CAD)", s: "с" },
  app: {
    title: "Приложение", connected: "подключено", waiting: "не подключено", loading: "загрузка…",
    loadingText: "Загрузка приложения… {s} с (при первом открытии ≈ {mb} МБ)", closedText: "Приложение загрузится при открытии панели",
  },
  kit: {
    title: "Скачать:", building: "Модель обновляется", soon: "файл через ~", sec: "с",
    linkPart: "Файлы детали", linkKit: "Файлы", linkStart: "Готовлю файлы, скачивание начнётся само…", linkDone: "Готово: файл скачан (папка «Загрузки»).",
    linkWait: "Модель только что изменилась, файлы собираются заново. Скачивание начнётся само, страницу не закрывайте. ",
    linkIn: "Осталось ~", linkSoon: "Осталось немного.", downloading: "Скачиваю…", queued: "В очереди, перед вами", of: "из", min: "мин",
    hint: "Файлы собираются по текущей модели. Нажмите кнопку ещё раз, чтобы не ждать; страницу можно закрыть — сборка продолжится, потом файл скачается сразу.",
    cancelled: "Ожидание отменено. Сборка продолжается — потом файл скачается сразу.", cancelHint: "Нажмите ещё раз, чтобы не ждать", failed: "Не удалось скачать, попробуйте ещё раз", part: "Скачать файлы детали", node: "Скачать архив узла",
    bambu: "Открыть в Bambu Studio", bambuNone: "У деталей для 3D-печати нет файлов STL", bambuHint: "Bambu Studio спросит, открыть ли файл с этого сайта, — ответьте «Да». Не открылся? Установите Bambu Studio с bambulab.com.",
    pdf: "Скачать PDF", close: "Закрыть", zoom: "Колесо мыши или два пальца — масштаб, перетаскивание — сдвиг",
    scopes: { print: "На 3D-печать", laser: "На лазер и гибку", bought: "Покупные (Excel)", step: "STEP модели", assembly: "Сборочные чертежи",
              electric: "Электрическая схема", pneumatic: "Схема пневматики" },
    off: { project: "Этот проект пока не настроен на выгрузку файлов", setup: "В этом проекте выгрузка этих файлов пока не настроена",
           print: "В модели нет деталей для 3D-печати", laser: "В модели нет деталей для лазерной резки и гибки",
           bought: "В модели нет покупных изделий", step: "В этом проекте выгрузка этих файлов пока не настроена",
           assembly: "В модели нет узлов с деталями для сборочного чертежа",
           electric: "Электрической схемы в этом проекте пока нет", pneumatic: "Схемы пневматики в этом проекте пока нет" },
  },
  fb: {
    open: "Нарисовать замечание", close: "Закрыть", ask: "Что здесь не так?", send: "Отправить", cancel: "Отмена",
    sent: "Отправлено ✓", failed: "Не отправилось, попробуйте ещё раз", rotate: "Поверните модель и дорисуйте с другой стороны: нарисованное остаётся на деталях", planeHint: "Нажмите на грань: дальше рисуете на её плоскости, в том числе за краем детали", clickHint: "Нажмите на деталь", dragHint: "Протяните от первой точки до второй",
    partAsk: "Что поменять в этой детали?", asmAsk: "Что поменять в этом узле?", mic: "Надиктовать", micStop: "Остановить запись",
    micBusy: "Распознаю…", micLong: "Запись слишком длинная, наговорите короче", micDenied: "Нет доступа к микрофону", micFailed: "Не удалось распознать, попробуйте ещё раз",
    tools: { select: "Выбор", pen: "Карандаш", line: "Линия", arrow: "Стрелка", rect: "Прямоугольник", ellipse: "Овал", text: "Текст", plane: "На плоскости грани", pin: "Булавка: точка на детали", move: "Отсюда сюда: перенести", face: "Эта грань", dim: "Размер между двумя точками" },
  },
  hw: {
    connect: "Подключить плату", noSerial: "Прошивка работает в Chrome или Edge на компьютере", noFw: "прошивка не опубликована",
    on: "подключена", off: "не подключена", flash: "Прошить", monitor: "Монитор", monitorOff: "Закрыть монитор", reset: "Сброс",
    bot: "Бот прошивает…", botReset: "Бот сбрасывает плату…", botMon: "Бот открывает монитор…", botIdent: "Бот определяет чип…", openingMon: "Открываю монитор…", resetting: "Сбрасываю…",
    noPortKey: "порт не найден (не подключён или не разрешён)", noFwName: "не указана прошивка (--fw)", flashing: "Прошиваю…", pick: "— выберите плату —", forCard: "для платы",
    uf2: "Скачать UF2", uf2Hint: "Зажмите кнопку BOOTSEL, подключите USB и перетащите файл на появившийся диск RPI-RP2.", busy: "плата занята другой операцией",
    notConnected: "плата не подключена", noPort: "Порт не выбран", flashed: "прошито", reseted: "плата сброшена", monOpened: "монитор открыт", espUsb: "ESP32 с USB", unknownDev: "неизвестное устройство",
  },
  hw2: { section: "Платы на столе", count: "Подключено: {n}", none: "Нет подключённых плат", unknown: "Неизвестная плата", notInModel: "нет в модели",
    which: "Какая это плата?", on: "Подключена", fw: "Прошивка", done: "Прошито", log: "Лог", mon: "Монитор", reset: "Сбросить плату", fold: "Свернуть / развернуть",
    flashTo: "Прошить на плату", realOn: "Плата на столе подключена", realOff: "Плата на столе не подключена", lines: "{n} стр." },
  flash: {
    noSerial: "Откройте в Chrome или Edge на компьютере: этот браузер не умеет работать с USB-портом.",
    noPort: "Порт не выбран. Нажмите кнопку ещё раз и выберите плату в списке.",
    busy: "Порт занят: его держит другая программа (Arduino IDE, монитор порта, другая вкладка). Закройте её и попробуйте снова.",
    noBoot: "Плата не вошла в режим прошивки. Зажмите кнопку BOOT на плате, нажмите «Прошить» и отпустите BOOT, когда пойдёт прогресс (если не помогло: BOOT зажата, коротко нажмите EN/RST). Проверьте также порт и кабель с данными.",
    noFile: "Не удалось скачать файл прошивки: ",
    monOpen: "-- монитор открыт, {b} бод --", monStop: "-- монитор остановлен: {e} --", monClosed: "-- монитор закрыт --",
    noPortSel: "Порт не выбран", resetNote: "-- сброс платы --", noFiles: "В манифесте нет файлов прошивки",
    avr2560: "atmega2560 пока не поддерживается для прошивки (нужен STK500v2). Монитор работает.",
    flashStart: "-- прошивка ({k}) --", unknownKind: "Неизвестный тип платы: {k}", done: "готово", flashDone: "-- прошивка завершена --",
    error: "ОШИБКА: {m}", identify: "Определяю чип…", chip: "Чип: {c}", retryBaud: "Сбой на {b} бод ({m}), повторяю на 115200…",
    connecting: "Подключаюсь к чипу…", stubSearch: "Нет ответа на 115200: ищу загрузчик прошлой попытки на {b} бод…",
    noFamily: "Не удалось определить семейство чипа («{c}»): прошивать не буду.", noChip: "В манифесте не указан чип: прошивать не буду.",
    wrongBoard: "Не та плата: на порту {have}, а прошивка для {want}.", fileN: "файл {i}/{n}",
    noAnswerBaud: "Нет ответа на {b} бод, пробую другую скорость…", stkNoAnswer: "STK500: нет ответа (sync timeout)",
    stkBad: "STK500: неожиданный ответ {r}", stkSync: "STK500 на {b} бод: сброс и синхронизация…", sig: "Сигнатура: {s}",
    noSigKnown: "Не знаю сигнатуру чипа «{c}» из манифеста: прошивать не буду.",
    noSigDetect: "Не удалось определить чип по сигнатуре {s}: прошивать не буду.", pageN: "страница {i}/{n}",
  },
};

const en = {
  reset: "Reset view", showAll: "Show all (hidden: {n})", hide: "Hide part", unhide: "Show part",
  hideAsm: "Hide assembly", unhideAsm: "Show assembly",
  hideStem: "Hide all like this ({n})", unhideStem: "Show all like this ({n})", center: "Rotate around this point",
  only: "Show only this", close: "Close", fold: "Collapse / expand", search: "Search: name or drawing number",
  step: "Download STEP", stl: "Download STL", loading: "Loading model… {pct}% ({a} of {b} MB)", parsing: "Reading model…",
  ready: "{m} parts in the assembly · {p} distinct · {t} M triangles", failed: "Could not load: {e}",
  drawing: "Drawing", kind: "Type", module: "Module", count: "In the machine", pcs: "{n} pcs", size: "Size X×Y×Z", mm: "mm",
  volume: "Volume", mass: "Mass (estimate)", massNote: "≈ {m} — estimate (volume × 7.85 g/cm³, material from the name)", massNoteDensity: "≈ {m} (estimate: volume × material density)", thickness: "Sheet thickness", cm3: "cm³", kg: "kg", g: "g",
  parts: "Parts in the assembly", asm: "Assembly", nothing: "Nothing found", more: "…and {n} more", distinct: "{n} distinct",
  kinds: { made: "made", standard: "standard", purchased: "purchased" },
  colorBy: "Colour:", byCad: "from CAD", byMaterial: "material", byKind: "type", cadNote: "Colours come from the CAD model (STEP).", material: "Material",
  view: "View:", edges: "Edges", ao: "Gap shadows", hc: "High contrast", matNote: " (from the name, not from CAD)", edgesLoading: "Edges… {pct}%",
  views: { front: "Front", back: "Back", left: "Left", right: "Right", top: "Top", bottom: "Bottom", iso: "Isometric" },
  grid: { on: "Grid", step: "Grid step:", auto: "auto", cell: "{n} mm", size: "Size: {s}" },
  meas: { on: "Ruler", hint: "click two points on the model", second: "second point…", clear: "Clear",
          out: "{d} mm", split: "horizontal {h} · vertical {v} mm" },
  aoOff: "Gap shadows were turned off automatically: a slow computer or phone. The button turns them back on.",
  materials: { steel: "steel, sheet metal", printed: "3D print (plastic)", rubber: "rubber, belts", fastener: "fasteners", bearing: "bearings",
    motor: "motors", electronics: "electronics", pneumatic: "pneumatics", glass: "glass", other: "other" },
  legendKinds: { made: "made", standard: "standard", purchased: "purchased" },
  hintDesk: "click — card · double-click — rotation centre · drag — rotate · right button or Shift — pan · wheel — zoom",
  hintTouch: "tap — card · double tap — rotation centre · one finger — rotate · two fingers — pan and zoom",
  decimal: ".",
  sec: { view: "View", hw: "Boards on the desk", dl: "Download files", parts: "Parts" },
  lang: "Language",
  banner: "The model is being updated…",
  clash: { head: "⚠ Parts overlap: {n}", tip: "The model check found parts that take the same space. ", fixing: "The AI Engineer is fixing it.",
           byCad: "The model was ported from your CAD", built: "The machine is already built",
           withYou: ": the AI Engineer goes through them with you and changes the model only with your consent.", mm3: "mm³", s: "s" },
  apk: { button: "Download the app (APK)", tip: "Android app, version {v}", ver: "Android, version {v}" },
  motion: { world: "Show the product", noCad: "not in CAD", source: "Source", unresolved: "CAD motion: assemblies not found — {n} (see the console)", s: "s" },
  device: "Device", keySpace: "Space",
  wires: { show: "Wires ({n})", tip: "The wires inside the machine along their routes, coloured by net (cut lengths in the purchase list)" },
  sim: {
    pause: "Pause", resume: "Resume", reset: "Reset", max: "max", clockTip: "Machine time (virtual)",
    ach: "actual ×{x}", lag: "×{x} — falling behind", maxAch: "max ≈×{x}", paused: "paused", crashed: "firmware crash", boards: "Boards", machine: "Simulation", flashReal: "Flash to the board",
    bstatus: { run: "running", idle: "idle", crash: "crash", paused: "paused" },
    mcu: "MCU", fw: "Firmware", fwNote: "Changing the firmware restarts the board and resets the machine.", status: "Status", role: "Role", cad: "Part in CAD",
    noCad: "not matched (cannot be told from CAD)", show3d: "Show in 3D", pins: "Pins: net / device, level", hideNums: "hide rows with weights",
    i2c: "I²C: this board's traffic", noI2c: "no traffic yet", reloading: "restarting…"
  },
  cycle: { play: "Play", pause: "Pause", t: "Cycle time, s", tip: "Machine cycle (scheduled from CAD)", s: "s" },
  app: {
    title: "App", connected: "connected", waiting: "not connected", loading: "loading…",
    loadingText: "Loading the app… {s} s (≈ {mb} MB the first time)", closedText: "The app loads when the panel is opened",
  },
  kit: {
    title: "Download:", building: "The model is being updated", soon: "file in ~", sec: "s",
    linkPart: "Part files", linkKit: "Files", linkStart: "Preparing the files, the download starts by itself…", linkDone: "Done: the file is downloaded (the Downloads folder).",
    linkWait: "The model has just changed, the files are being rebuilt. The download starts by itself, keep this page open. ",
    linkIn: "Remaining ~", linkSoon: "Almost done.", downloading: "Downloading…", queued: "In the queue, ahead of you:", of: "of", min: "min",
    hint: "The files are built from the current model. Press the button again to stop waiting; you may close the page — the build goes on, and later the file downloads at once.",
    cancelled: "Waiting cancelled. The build goes on — later the file downloads at once.", cancelHint: "Press again to stop waiting", failed: "Download failed, please try again", part: "Download the part files", node: "Download the assembly archive",
    bambu: "Open in Bambu Studio", bambuNone: "The 3D-print parts have no STL files", bambuHint: "Bambu Studio asks whether to open a file from this site: answer «Yes». Nothing opened? Install Bambu Studio from bambulab.com.",
    pdf: "Download PDF", close: "Close", zoom: "Mouse wheel or two fingers to zoom, drag to move",
    scopes: { print: "For 3D printing", laser: "For laser cutting and bending", bought: "Purchased parts (Excel)", step: "Model (STEP)", assembly: "Assembly drawings",
              electric: "Wiring diagram", pneumatic: "Pneumatic diagram" },
    off: { project: "File export is not set up for this project yet", setup: "Export of these files is not set up in this project yet",
           print: "The model has no parts for 3D printing", laser: "The model has no parts for laser cutting and bending",
           bought: "The model has no purchased parts", step: "Export of these files is not set up in this project yet",
           assembly: "The model has no assemblies with parts for an assembly drawing",
           electric: "This project has no wiring diagram yet", pneumatic: "This project has no pneumatic diagram yet" },
  },
  fb: {
    open: "Draw a note", close: "Close", ask: "What is wrong here?", send: "Send", cancel: "Cancel",
    sent: "Sent ✓", failed: "Not sent, please try again", rotate: "Rotate the model and add more from another side: what you drew stays on the parts", planeHint: "Click a face: then you draw on its plane, also past the edge of the part", clickHint: "Click a part", dragHint: "Drag from the first point to the second",
    partAsk: "What should change in this part?", asmAsk: "What should change in this assembly?", mic: "Dictate", micStop: "Stop recording",
    micBusy: "Recognising…", micLong: "The recording is too long, please say it shorter", micDenied: "No access to the microphone", micFailed: "Could not recognise it, please try again",
    tools: { select: "Select", pen: "Pencil", line: "Line", arrow: "Arrow", rect: "Rectangle", ellipse: "Ellipse", text: "Text", plane: "On a face's plane", pin: "Pin: a point on a part", move: "From here to there: move", face: "This face", dim: "Distance between two points" },
  },
  hw: {
    connect: "Connect a board", noSerial: "Flashing works in Chrome or Edge on a computer", noFw: "firmware not published",
    on: "connected", off: "not connected", flash: "Flash", monitor: "Monitor", monitorOff: "Close the monitor", reset: "Reset",
    bot: "The bot is flashing…", botReset: "The bot is resetting the board…", botMon: "The bot is opening the monitor…", botIdent: "The bot is identifying the chip…", openingMon: "Opening the monitor…", resetting: "Resetting…",
    noPortKey: "port not found (not connected or not allowed)", noFwName: "no firmware given (--fw)", flashing: "Flashing…", pick: "— pick a board —", forCard: "for the board",
    uf2: "Download UF2", uf2Hint: "Hold the BOOTSEL button, plug in the USB and drag the file onto the RPI-RP2 drive that appears.", busy: "the board is busy with another operation",
    notConnected: "the board is not connected", noPort: "No port selected", flashed: "flashed", reseted: "board reset", monOpened: "monitor open", espUsb: "ESP32 with USB", unknownDev: "unknown device",
  },
  hw2: { section: "Boards on the desk", count: "Connected: {n}", none: "No boards connected", unknown: "Unknown board", notInModel: "not in the model",
    which: "Which board is this?", on: "Connected", fw: "Firmware", done: "Flashed", log: "Log", mon: "Monitor", reset: "Reset the board", fold: "Collapse / expand",
    flashTo: "Flash to the board", realOn: "The board on the desk is connected", realOff: "The board on the desk is not connected", lines: "{n} line|lines" },
  flash: {
    noSerial: "Open this in Chrome or Edge on a computer: this browser cannot use a USB port.",
    noPort: "No port selected. Press the button again and pick the board in the list.",
    busy: "The port is busy: another program holds it (Arduino IDE, a serial monitor, another tab). Close it and try again.",
    noBoot: "The board did not enter flashing mode. Hold BOOT on the board, press «Flash» and release BOOT when the progress starts (if that does not help: with BOOT held, briefly press EN/RST). Check the port and that the cable carries data.",
    noFile: "Could not download the firmware file: ",
    monOpen: "-- monitor open, {b} baud --", monStop: "-- monitor stopped: {e} --", monClosed: "-- monitor closed --",
    noPortSel: "No port selected", resetNote: "-- board reset --", noFiles: "The manifest lists no firmware files",
    avr2560: "atmega2560 cannot be flashed yet (needs STK500v2). The monitor works.",
    flashStart: "-- flashing ({k}) --", unknownKind: "Unknown board type: {k}", done: "done", flashDone: "-- flashing finished --",
    error: "ERROR: {m}", identify: "Identifying the chip…", chip: "Chip: {c}", retryBaud: "Failed at {b} baud ({m}), retrying at 115200…",
    connecting: "Connecting to the chip…", stubSearch: "No answer at 115200: looking for the loader of the last attempt at {b} baud…",
    noFamily: "Could not tell the chip family («{c}»): not flashing.", noChip: "The manifest names no chip: not flashing.",
    wrongBoard: "Wrong board: the port has {have}, the firmware is for {want}.", fileN: "file {i}/{n}",
    noAnswerBaud: "No answer at {b} baud, trying another speed…", stkNoAnswer: "STK500: no answer (sync timeout)",
    stkBad: "STK500: unexpected answer {r}", stkSync: "STK500 at {b} baud: reset and sync…", sig: "Signature: {s}",
    noSigKnown: "Unknown signature for the chip «{c}» from the manifest: not flashing.",
    noSigDetect: "Could not tell the chip from signature {s}: not flashing.", pageN: "page {i}/{n}",
  },
};

const pl = {
  reset: "Resetuj widok", showAll: "Pokaż wszystkie (ukryte: {n})", hide: "Ukryj część", unhide: "Przywróć część",
  hideAsm: "Ukryj zespół", unhideAsm: "Przywróć zespół",
  hideStem: "Ukryj wszystkie takie ({n})", unhideStem: "Przywróć wszystkie takie ({n})", center: "Środek obrotu tutaj",
  only: "Pokaż tylko to", close: "Zamknij", fold: "Zwiń / rozwiń", search: "Szukaj: nazwa lub numer rysunku",
  step: "Pobierz STEP", stl: "Pobierz STL", loading: "Ładowanie modelu… {pct}% ({a} z {b} MB)", parsing: "Wczytywanie modelu…",
  ready: "{m} części w złożeniu · {p} różnych · {t} mln trójkątów", failed: "Nie udało się załadować: {e}",
  drawing: "Rysunek", kind: "Typ", module: "Moduł", count: "W maszynie", pcs: "{n} szt.", size: "Wymiary X×Y×Z", mm: "mm",
  volume: "Objętość", mass: "Masa (szacunkowa)", massNote: "≈ {m} — szacunek (objętość × 7,85 g/cm³, materiał wg nazwy)", massNoteDensity: "≈ {m} (szacunek: objętość × gęstość materiału)", thickness: "Grubość blachy", cm3: "cm³", kg: "kg", g: "g",
  parts: "Części w zespole", asm: "Zespół", nothing: "Nic nie znaleziono", more: "…i jeszcze {n}", distinct: "{n} różnych",
  kinds: { made: "wykonywana", standard: "znormalizowana", purchased: "kupowana" },
  colorBy: "Kolor:", byCad: "z CAD", byMaterial: "materiał", byKind: "typ", cadNote: "Kolory pochodzą z modelu CAD (STEP).", material: "Materiał",
  view: "Widok:", edges: "Krawędzie", ao: "Cienie w szczelinach", hc: "Wysoki kontrast", matNote: " (wg nazwy, nie z CAD)", edgesLoading: "Krawędzie… {pct}%",
  views: { front: "Z przodu", back: "Z tyłu", left: "Z lewej", right: "Z prawej", top: "Z góry", bottom: "Z dołu", iso: "Izometria" },
  grid: { on: "Siatka", step: "Krok siatki:", auto: "auto", cell: "{n} mm", size: "Wymiary: {s}" },
  meas: { on: "Linijka", hint: "proszę kliknąć dwa punkty na modelu", second: "drugi punkt…", clear: "Wyczyść",
          out: "{d} mm", split: "w poziomie {h} · w pionie {v} mm" },
  aoOff: "Cienie w szczelinach wyłączono automatycznie: słaby komputer lub telefon. Przycisk włącza je ponownie.",
  materials: { steel: "stal, blacha", printed: "druk 3D (tworzywo)", rubber: "guma, pasy", fastener: "elementy złączne", bearing: "łożyska",
    motor: "silniki", electronics: "elektronika", pneumatic: "pneumatyka", glass: "szkło", other: "inne" },
  legendKinds: { made: "wykonywana", standard: "znormalizowana", purchased: "kupowana" },
  hintDesk: "klik — karta · podwójny klik — środek obrotu · przeciągnij — obrót · prawy przycisk lub Shift — przesunięcie · kółko — skala",
  hintTouch: "dotknij — karta · dotknij dwa razy — środek obrotu · jeden palec — obrót · dwa palce — przesunięcie i skala",
  decimal: ",",
  sec: { view: "Widok", hw: "Płytki na biurku", dl: "Pobierz pliki", parts: "Części" },
  lang: "Język",
  banner: "Model jest aktualizowany…",
  clash: { head: "⚠ Części na siebie nachodzą: {n}", tip: "Kontrola modelu znalazła części zajmujące to samo miejsce. ", fixing: "AI Inżynier to poprawia.",
           byCad: "Model przeniesiono z Państwa CAD", built: "Maszyna jest już zbudowana",
           withYou: ": AI Inżynier omawia je z Państwem i zmienia model tylko za Państwa zgodą.", mm3: "mm³", s: "s" },
  apk: { button: "Pobierz aplikację (APK)", tip: "Aplikacja na Androida, wersja {v}", ver: "Android, wersja {v}" },
  motion: { world: "Pokaż wyrób", noCad: "brak w CAD", source: "Źródło", unresolved: "Ruch z CAD: nie znaleziono zespołów — {n} (zob. konsola)", s: "s" },
  device: "Urządzenie", keySpace: "Spacja",
  wires: { show: "Przewody ({n})", tip: "Przewody wewnątrz maszyny wzdłuż ich tras, kolor wg obwodu (długości odcinków na liście zakupów)" },
  sim: {
    pause: "Pauza", resume: "Wznów", reset: "Reset", max: "maks", clockTip: "Czas maszyny (wirtualny)",
    ach: "faktycznie ×{x}", lag: "×{x} — nie nadąża", maxAch: "maks ≈×{x}", paused: "wstrzymano", crashed: "błąd firmware", boards: "Płytki", machine: "Symulacja", flashReal: "Wgraj na płytkę",
    bstatus: { run: "działa", idle: "czeka", crash: "błąd", paused: "pauza" },
    mcu: "MCU", fw: "Firmware", fwNote: "Zmiana firmware restartuje płytkę i resetuje maszynę.", status: "Status", role: "Rola", cad: "Część w CAD",
    noCad: "nieprzypisana (nie da się ustalić z CAD)", show3d: "Pokaż w 3D", pins: "Piny: sieć / urządzenie, poziom", hideNums: "ukryj wiersze z wagami",
    i2c: "I²C: komunikacja tej płytki", noI2c: "na razie brak komunikacji", reloading: "restart…"
  },
  cycle: { play: "Start", pause: "Pauza", t: "Czas cyklu, s", tip: "Cykl maszyny (wg harmonogramu z CAD)", s: "s" },
  app: {
    title: "Aplikacja", connected: "połączona", waiting: "niepołączona", loading: "ładowanie…",
    loadingText: "Ładowanie aplikacji… {s} s (za pierwszym razem ≈ {mb} MB)", closedText: "Aplikacja załaduje się po otwarciu panelu",
  },
  kit: {
    title: "Pobierz:", building: "Model jest aktualizowany", soon: "plik za ~", sec: "s",
    linkPart: "Pliki części", linkKit: "Pliki", linkStart: "Przygotowuję pliki, pobieranie zacznie się samo…", linkDone: "Gotowe: plik pobrany (folder „Pobrane”).",
    linkWait: "Model właśnie się zmienił, pliki są budowane od nowa. Pobieranie zacznie się samo, proszę nie zamykać strony. ",
    linkIn: "Zostało ~", linkSoon: "Już prawie.", downloading: "Pobieram…", queued: "W kolejce, przed Państwem:", of: "z", min: "min",
    hint: "Pliki są budowane z aktualnego modelu. Proszę kliknąć przycisk jeszcze raz, aby nie czekać; stronę można zamknąć — budowanie trwa dalej, a potem plik pobierze się od razu.",
    cancelled: "Czekanie anulowane. Budowanie trwa dalej — potem plik pobierze się od razu.", cancelHint: "Proszę kliknąć jeszcze raz, aby nie czekać", failed: "Nie udało się pobrać, proszę spróbować ponownie", part: "Pobierz pliki części", node: "Pobierz archiwum zespołu",
    bambu: "Otwórz w Bambu Studio", bambuNone: "Części do druku 3D nie mają plików STL", bambuHint: "Bambu Studio zapyta, czy otworzyć plik z tej strony — proszę odpowiedzieć „Tak”. Nic się nie otworzyło? Proszę zainstalować Bambu Studio z bambulab.com.",
    pdf: "Pobierz PDF", close: "Zamknij", zoom: "Kółko myszy lub dwa palce — powiększenie, przeciąganie — przesunięcie",
    scopes: { print: "Do druku 3D", laser: "Do lasera i gięcia", bought: "Kupowane (Excel)", step: "Model STEP", assembly: "Rysunki złożeniowe",
              electric: "Schemat elektryczny", pneumatic: "Schemat pneumatyczny" },
    off: { project: "W tym projekcie eksport plików nie jest jeszcze skonfigurowany", setup: "W tym projekcie eksport tych plików nie jest jeszcze skonfigurowany",
           print: "W modelu nie ma części do druku 3D", laser: "W modelu nie ma części do cięcia laserowego i gięcia",
           bought: "W modelu nie ma części kupowanych", step: "W tym projekcie eksport tych plików nie jest jeszcze skonfigurowany",
           assembly: "W modelu nie ma zespołów z częściami do rysunku złożeniowego",
           electric: "Ten projekt nie ma jeszcze schematu elektrycznego", pneumatic: "Ten projekt nie ma jeszcze schematu pneumatycznego" },
  },
  fb: {
    open: "Narysuj uwagę", close: "Zamknij", ask: "Co tu jest nie tak?", send: "Wyślij", cancel: "Anuluj",
    sent: "Wysłano ✓", failed: "Nie wysłano, proszę spróbować ponownie", rotate: "Proszę obrócić model i dorysować z innej strony: narysowane zostaje na częściach", planeHint: "Proszę kliknąć ścianę: potem rysuje się na jej płaszczyźnie, także za krawędzią części", clickHint: "Proszę kliknąć część", dragHint: "Proszę przeciągnąć od pierwszego punktu do drugiego",
    partAsk: "Co zmienić w tej części?", asmAsk: "Co zmienić w tym zespole?", mic: "Podyktuj", micStop: "Zatrzymaj nagrywanie",
    micBusy: "Rozpoznaję…", micLong: "Nagranie jest za długie, proszę mówić krócej", micDenied: "Brak dostępu do mikrofonu", micFailed: "Nie udało się rozpoznać, proszę spróbować ponownie",
    tools: { select: "Wybór", pen: "Ołówek", line: "Linia", arrow: "Strzałka", rect: "Prostokąt", ellipse: "Elipsa", text: "Tekst", plane: "Na płaszczyźnie ściany", pin: "Pinezka: punkt na części", move: "Stąd tam: przenieść", face: "Ta ściana", dim: "Wymiar między dwoma punktami" },
  },
  hw: {
    connect: "Podłącz płytkę", noSerial: "Wgrywanie działa w Chrome lub Edge na komputerze", noFw: "firmware nie jest opublikowany",
    on: "podłączona", off: "niepodłączona", flash: "Wgraj", monitor: "Monitor", monitorOff: "Zamknij monitor", reset: "Reset",
    bot: "Bot wgrywa…", botReset: "Bot resetuje płytkę…", botMon: "Bot otwiera monitor…", botIdent: "Bot rozpoznaje układ…", openingMon: "Otwieram monitor…", resetting: "Resetuję…",
    noPortKey: "nie znaleziono portu (niepodłączony lub bez zgody)", noFwName: "nie podano firmware (--fw)", flashing: "Wgrywam…", pick: "— wybierz płytkę —", forCard: "dla płytki",
    uf2: "Pobierz UF2", uf2Hint: "Proszę przytrzymać przycisk BOOTSEL, podłączyć USB i przeciągnąć plik na dysk RPI-RP2, który się pojawi.", busy: "płytka jest zajęta inną operacją",
    notConnected: "płytka nie jest podłączona", noPort: "Nie wybrano portu", flashed: "wgrano", reseted: "płytka zresetowana", monOpened: "monitor otwarty", espUsb: "ESP32 z USB", unknownDev: "nieznane urządzenie",
  },
  hw2: { section: "Płytki na biurku", count: "Podłączono: {n}", none: "Brak podłączonych płytek", unknown: "Nieznana płytka", notInModel: "brak w modelu",
    which: "Która to płytka?", on: "Podłączona", fw: "Firmware", done: "Wgrano", log: "Log", mon: "Monitor", reset: "Resetuj płytkę", fold: "Zwiń / rozwiń",
    flashTo: "Wgraj na płytkę", realOn: "Płytka na biurku jest podłączona", realOff: "Płytka na biurku nie jest podłączona", lines: "{n} wiersz|wiersze|wierszy" },
  flash: {
    noSerial: "Proszę otworzyć w Chrome lub Edge na komputerze: ta przeglądarka nie obsługuje portu USB.",
    noPort: "Nie wybrano portu. Proszę kliknąć przycisk jeszcze raz i wybrać płytkę z listy.",
    busy: "Port jest zajęty: trzyma go inny program (Arduino IDE, monitor portu, inna karta). Proszę go zamknąć i spróbować ponownie.",
    noBoot: "Płytka nie weszła w tryb wgrywania. Proszę przytrzymać BOOT na płytce, kliknąć „Wgraj” i puścić BOOT, gdy ruszy postęp (jeśli nie pomaga: z wciśniętym BOOT krótko nacisnąć EN/RST). Warto też sprawdzić port i czy kabel przesyła dane.",
    noFile: "Nie udało się pobrać pliku firmware: ",
    monOpen: "-- monitor otwarty, {b} bodów --", monStop: "-- monitor zatrzymany: {e} --", monClosed: "-- monitor zamknięty --",
    noPortSel: "Nie wybrano portu", resetNote: "-- reset płytki --", noFiles: "Manifest nie zawiera plików firmware",
    avr2560: "atmega2560 nie da się jeszcze wgrać (potrzebny STK500v2). Monitor działa.",
    flashStart: "-- wgrywanie ({k}) --", unknownKind: "Nieznany typ płytki: {k}", done: "gotowe", flashDone: "-- wgrywanie zakończone --",
    error: "BŁĄD: {m}", identify: "Rozpoznaję układ…", chip: "Układ: {c}", retryBaud: "Błąd przy {b} bodach ({m}), ponawiam przy 115200…",
    connecting: "Łączę się z układem…", stubSearch: "Brak odpowiedzi przy 115200: szukam programu ładującego z poprzedniej próby przy {b} bodach…",
    noFamily: "Nie udało się ustalić rodziny układu („{c}”): nie wgrywam.", noChip: "Manifest nie podaje układu: nie wgrywam.",
    wrongBoard: "Nie ta płytka: na porcie jest {have}, a firmware jest dla {want}.", fileN: "plik {i}/{n}",
    noAnswerBaud: "Brak odpowiedzi przy {b} bodach, próbuję innej prędkości…", stkNoAnswer: "STK500: brak odpowiedzi (sync timeout)",
    stkBad: "STK500: nieoczekiwana odpowiedź {r}", stkSync: "STK500 przy {b} bodach: reset i synchronizacja…", sig: "Sygnatura: {s}",
    noSigKnown: "Nieznana sygnatura układu „{c}” z manifestu: nie wgrywam.",
    noSigDetect: "Nie udało się rozpoznać układu po sygnaturze {s}: nie wgrywam.", pageN: "strona {i}/{n}",
  },
};

export const DICT = { ru, en, pl };

// Words in part names (any language: a model's names are in the language it was drawn in, not the page's), as regex alternatives.
// glass: guards and windows are drawn transparent and never take a click from the part behind; a bracket of a guard
// ("Уголок шторки") does not match, so it stays opaque.
export const NAME_WORDS = {
  glass: { ru: "шторка защитная|плексиглас|оргстекл|стекл", en: "plexi|glass", it: "vetro" },
};
