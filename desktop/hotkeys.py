"""Глобальные горячие клавиши: Герта доступна из любого окна.

Библиотека keyboard работает на уровне системного хука, поэтому сочетания
ловятся, даже когда окно ассистента скрыто. Хук живёт в своём потоке, а до
интерфейса события доходят через Qt-сигналы.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HotkeyBinding:
    combination: str
    description: str


class GlobalHotkeys(QObject):
    """Мост между системным хуком клавиатуры и Qt.

    Сигналы приходят из потока хука, поэтому получатели обязаны быть QObject -
    иначе слот выполнится не в главном потоке и интерфейс упадёт.
    """

    summon_requested = Signal()          # показать/скрыть окно
    dictation_started = Signal()         # зажали клавишу диктовки
    dictation_stopped = Signal()         # отпустили
    voice_toggle_requested = Signal()    # включить/выключить голосовой режим
    ask_selection_requested = Signal()   # разобрать выделенное в редакторе

    def __init__(
        self,
        *,
        summon: str,
        dictation: str,
        voice_toggle: str,
        ask_selection: str = '',
    ) -> None:
        super().__init__()
        self.bindings = {
            'summon': HotkeyBinding(summon, 'показать окно Герты'),
            'dictation': HotkeyBinding(dictation, 'диктовка в активное окно (удерживать)'),
            'voice_toggle': HotkeyBinding(voice_toggle, 'голосовой режим'),
        }
        if ask_selection:
            self.bindings['ask_selection'] = HotkeyBinding(ask_selection, 'разобрать выделенный код')
        self._handles: list = []
        self._dictating = False
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def start(self) -> bool:
        try:
            import keyboard
        except ImportError:
            logger.warning("Пакет 'keyboard' не установлен, глобальные хоткеи недоступны.")
            return False

        try:
            self._handles.append(
                keyboard.add_hotkey(
                    self.bindings['summon'].combination,
                    self.summon_requested.emit,
                    suppress=False,
                )
            )
            self._handles.append(
                keyboard.add_hotkey(
                    self.bindings['voice_toggle'].combination,
                    self.voice_toggle_requested.emit,
                    suppress=False,
                )
            )
            if 'ask_selection' in self.bindings:
                self._handles.append(
                    keyboard.add_hotkey(
                        self.bindings['ask_selection'].combination,
                        self.ask_selection_requested.emit,
                        suppress=False,
                    )
                )
            # Диктовка работает на удержание, поэтому нажатие и отпускание ловим отдельно.
            keyboard.on_press_key(
                _last_key(self.bindings['dictation'].combination),
                self._on_dictation_press,
                suppress=False,
            )
            keyboard.on_release_key(
                _last_key(self.bindings['dictation'].combination),
                self._on_dictation_release,
                suppress=False,
            )
        except Exception as exc:
            # Чаще всего это отсутствие прав: системный хук требует администратора.
            logger.warning('Не удалось повесить глобальные хоткеи: %s', exc)
            return False

        self._available = True
        logger.info(
            'Глобальные хоткеи: %s — окно, %s — диктовка (удержание), %s — голосовой режим.',
            self.bindings['summon'].combination,
            self.bindings['dictation'].combination,
            self.bindings['voice_toggle'].combination,
        )
        return True

    def _modifiers_held(self) -> bool:
        """Проверяет, что зажаты все модификаторы сочетания диктовки."""
        import keyboard

        parts = [part.strip().lower() for part in self.bindings['dictation'].combination.split('+')]
        return all(keyboard.is_pressed(part) for part in parts[:-1]) if len(parts) > 1 else True

    def _on_dictation_press(self, event) -> None:
        del event
        if self._dictating or not self._modifiers_held():
            return
        self._dictating = True
        self.dictation_started.emit()

    def _on_dictation_release(self, event) -> None:
        del event
        if not self._dictating:
            return
        self._dictating = False
        self.dictation_stopped.emit()

    def stop(self) -> None:
        if not self._available:
            return
        try:
            import keyboard

            keyboard.unhook_all_hotkeys()
        except Exception as exc:  # pragma: no cover
            logger.debug('Не удалось снять хоткеи: %s', exc)
        self._available = False


def _last_key(combination: str) -> str:
    """'ctrl+shift+d' -> 'd': keyboard слушает нажатие конкретной клавиши."""
    return combination.split('+')[-1].strip().lower()
