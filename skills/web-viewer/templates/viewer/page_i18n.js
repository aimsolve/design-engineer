// UI texts of the one-file viewer (page.tpl.html), one table per language; build.py inlines this file. The page takes ?lang=, else the
// hook's `lang`, else ru; the hook's `labels` override single keys on top.
var VIEWER_I18N = {
  ru: {
    pause: "Пауза", play: "Пуск", step: "Шаг ▸", slow: "медленно ×5", reset: "Сбросить вид", view: "{name}",
    showAll: "Показать все (скрыто: {n})", hide: "Скрыть деталь", unhide: "Вернуть деталь",
    hideStem: "Скрыть все такие ({n})", center: "Центр вращения сюда", close: "Закрыть", fold: "Свернуть / развернуть",
    part: "деталь", motion: "движение", size: "габарит X×Y×Z", centre: "центр", mm: "мм", still: "неподвижная",
    time: "t = {t} с", decimal: ",",
    hintDesk: "клик — карточка · двойной клик — центр вращения · тащить — вращать · правая кнопка или Shift — сдвиг · колесо — масштаб",
    hintTouch: "тап — карточка · двойной тап — центр вращения · один палец — вращать · два пальца — сдвиг и масштаб"
  },
  en: {
    pause: "Pause", play: "Play", step: "Step ▸", slow: "slow ×5", reset: "Reset view", view: "{name}",
    showAll: "Show all (hidden: {n})", hide: "Hide part", unhide: "Show part",
    hideStem: "Hide all like this ({n})", center: "Rotate around this point", close: "Close", fold: "Collapse / expand",
    part: "part", motion: "motion", size: "size X×Y×Z", centre: "centre", mm: "mm", still: "static",
    time: "t = {t} s", decimal: ".",
    hintDesk: "click — card · double-click — rotation centre · drag — rotate · right button or Shift — pan · wheel — zoom",
    hintTouch: "tap — card · double tap — rotation centre · one finger — rotate · two fingers — pan and zoom"
  }
};
