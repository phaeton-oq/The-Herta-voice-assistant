"""Окно настроек: выбор режима работы и ключи API.

Смысл в том, чтобы не заставлять человека редактировать `.env` руками.
Ключи уходят в системное хранилище (см. utils/key_store), режим применяется
без перезапуска.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import styles as T
from utils import key_store

# Человеческие названия провайдеров и модели по умолчанию для каждого.
PROVIDERS: tuple[tuple[str, str, str], ...] = (
    ('cerebras', 'Cerebras — облако, быстро', 'CEREBRAS_API_KEY'),
    ('ollama', 'Ollama — локально, без интернета', ''),
    ('google_ai', 'Google AI Studio — облако', 'GOOGLE_AI_API_KEY'),
    ('deepseek', 'DeepSeek / OpenRouter — облако', 'DEEPSEEK_API_KEY'),
)


class SecretRow(QWidget):
    """Одна строка с ключом: поле, источник и кнопки."""

    changed = Signal()

    def __init__(self, slot: key_store.SecretSlot) -> None:
        super().__init__()
        self.slot = slot

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText(f'{slot.label}: вставь ключ')
        top.addWidget(self.field, 1)

        self.show_button = QPushButton('Показать')
        self.show_button.setCheckable(True)
        self.show_button.toggled.connect(self._on_show_toggled)
        top.addWidget(self.show_button)

        self.save_button = QPushButton('Сохранить')
        self.save_button.clicked.connect(self._on_save)
        top.addWidget(self.save_button)

        self.clear_button = QPushButton('Удалить')
        self.clear_button.clicked.connect(self._on_delete)
        top.addWidget(self.clear_button)

        layout.addLayout(top)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f'color: {T.TEXT_DIM}; font-size: 11px;')
        layout.addWidget(self.status)

        self.refresh()

    def _on_show_toggled(self, shown: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        self.field.setEchoMode(mode)
        self.show_button.setText('Скрыть' if shown else 'Показать')

    def refresh(self) -> None:
        """Перечитывает текущее значение и показывает, откуда оно взято."""
        value, source = key_store.read(self.slot.env)
        self.field.clear()

        if source == key_store.SOURCE_KEYRING:
            self._set_status(
                f'Задан в системном хранилище: {key_store.mask(value)}. {self.slot.hint}', ok=True
            )
            self.clear_button.setEnabled(True)
        elif source == key_store.SOURCE_ENV:
            # Важно сказать прямо: сохранённый здесь ключ перекроет .env,
            # иначе человек не поймёт, почему правка файла перестала влиять.
            self._set_status(
                f'Задан в .env: {key_store.mask(value)}. Сохранённый здесь ключ будет главнее. '
                f'{self.slot.hint}',
                ok=True,
            )
            self.clear_button.setEnabled(False)
        else:
            self._set_status(f'Не задан. {self.slot.hint}', ok=False)
            self.clear_button.setEnabled(False)

        if not key_store.available():
            self.save_button.setEnabled(False)
            self._set_status(
                'Системное хранилище недоступно — ключи придётся держать в .env. '
                'Обычно помогает `pip install keyring`.',
                ok=False,
            )

    def _set_status(self, text: str, *, ok: bool) -> None:
        color = T.TEXT_DIM if ok else T.WARN
        self.status.setStyleSheet(f'color: {color}; font-size: 11px;')
        self.status.setText(text)

    def _on_save(self) -> None:
        value = self.field.text().strip()
        if not value:
            self._set_status('Пустой ключ сохранять нечего.', ok=False)
            return
        if key_store.write(self.slot.env, value):
            self.refresh()
            self.changed.emit()
        else:
            self._set_status('Не удалось сохранить в системное хранилище.', ok=False)

    def _on_delete(self) -> None:
        key_store.delete(self.slot.env)
        self.refresh()
        self.changed.emit()


class SettingsDialog(QDialog):
    """Режим работы и ключи. Применение режима делает вызывающая сторона."""

    provider_apply_requested = Signal(str, str)
    keys_changed = Signal()
    skills_changed = Signal()

    def __init__(
        self,
        parent: QWidget | None,
        *,
        provider: str,
        models: dict[str, str],
        library=None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self.setWindowTitle('Настройки Герты')
        self.setModal(True)
        self.setMinimumWidth(560)
        self._models = dict(models)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # --- Режим работы ---
        root.addWidget(self._section_title('Режим работы'))

        form = QFormLayout()
        form.setSpacing(8)
        self.provider_box = QComboBox()
        for name, label, _ in PROVIDERS:
            self.provider_box.addItem(label, name)
        index = self.provider_box.findData(provider)
        if index >= 0:
            self.provider_box.setCurrentIndex(index)
        self.provider_box.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow('Провайдер:', self.provider_box)

        self.model_field = QLineEdit()
        form.addRow('Модель:', self.model_field)
        root.addLayout(form)

        self.provider_hint = QLabel()
        self.provider_hint.setWordWrap(True)
        self.provider_hint.setStyleSheet(f'color: {T.TEXT_DIM}; font-size: 11px;')
        root.addWidget(self.provider_hint)

        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        self.apply_button = QPushButton('Применить режим')
        self.apply_button.clicked.connect(self._on_apply)
        apply_row.addWidget(self.apply_button)
        root.addLayout(apply_row)

        root.addWidget(self._separator())

        # --- Ключи ---
        root.addWidget(self._section_title('Ключи API'))
        storage_note = QLabel(
            'Ключи хранятся в системном хранилище учётных данных, а не в файле проекта.'
            if key_store.available()
            else 'Системное хранилище недоступно, ключи читаются только из .env.'
        )
        storage_note.setWordWrap(True)
        storage_note.setStyleSheet(f'color: {T.TEXT_DIM}; font-size: 11px;')
        root.addWidget(storage_note)

        self.rows: list[SecretRow] = []
        for slot in key_store.SLOTS:
            row = SecretRow(slot)
            row.changed.connect(self.keys_changed)
            self.rows.append(row)
            root.addWidget(self._labelled(slot.label, row))

        # --- Навыки ---
        if self._library is not None and self._library.skills:
            root.addWidget(self._separator())
            root.addWidget(self._section_title('Навыки'))
            hint = QLabel(
                'Выключенный навык остаётся на диске, но не подмешивается в разговор '
                'и не занимает место в постоянном промпте.'
            )
            hint.setWordWrap(True)
            hint.setStyleSheet(f'color: {T.TEXT_DIM}; font-size: 11px;')
            root.addWidget(hint)

            for skill in self._library.skills:
                box = QCheckBox(f'{skill.title} — {skill.description}')
                box.setChecked(self._library.is_enabled(skill.name))
                box.toggled.connect(
                    lambda checked, name=skill.name: self._on_skill_toggled(name, checked)
                )
                box.setStyleSheet(f'color: {T.TEXT}; font-size: 12px;')
                root.addWidget(box)

        root.addStretch(1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton('Закрыть')
        close_button.clicked.connect(self.accept)
        close_row.addWidget(close_button)
        root.addLayout(close_row)

        self._on_provider_changed()

    # ---------- вспомогательное оформление ----------

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f'color: {T.GOLD}; font-weight: 600; letter-spacing: 1px;')
        return label

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f'color: {T.LINE};')
        return line

    def _labelled(self, title: str, widget: QWidget) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        caption = QLabel(title)
        caption.setStyleSheet(f'color: {T.TEXT}; font-size: 12px;')
        layout.addWidget(caption)
        layout.addWidget(widget)
        return holder

    # ---------- поведение ----------

    def _on_provider_changed(self) -> None:
        provider = self.provider_box.currentData()
        self.model_field.setText(self._models.get(provider, ''))

        needed_key = ''
        for name, _, env in PROVIDERS:
            if name == provider:
                needed_key = env
                break

        if not needed_key:
            self.provider_hint.setText(
                'Локальный режим: нужен запущенный Ollama и загруженная модель. '
                'Ключ не требуется, интернет тоже. Занимает видеопамять.'
            )
            return

        value, source = key_store.read(needed_key)
        if value:
            where = 'системного хранилища' if source == key_store.SOURCE_KEYRING else '.env'
            self.provider_hint.setText(f'Ключ найден, взят из {where}. Можно применять.')
        else:
            self.provider_hint.setText(
                'Ключа для этого провайдера нет — впиши его ниже, иначе режим не заработает.'
            )

    def _on_apply(self) -> None:
        provider = self.provider_box.currentData()
        model = self.model_field.text().strip()
        self._models[provider] = model
        self.provider_apply_requested.emit(provider, model)

    def _on_skill_toggled(self, name: str, enabled: bool) -> None:
        """Переключение сохраняется сразу: отдельной кнопки «применить» нет."""
        if self._library is None:
            return
        self._library.set_enabled(name, enabled)
        self.skills_changed.emit()

    def refresh_keys(self) -> None:
        for row in self.rows:
            row.refresh()
        self._on_provider_changed()
