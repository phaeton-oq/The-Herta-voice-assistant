"""Инструмент "посмотри на экран" для локальных режимов (голос, GUI, консоль)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from actions.tool_layer import CallableTool, ToolParameter, ToolResult, ToolSpec
from actions.vision import VisionProvider, build_vision_context

logger = logging.getLogger(__name__)


class VisionToolProvider:
    def __init__(self, provider: VisionProvider) -> None:
        self.provider = provider

    def callable_tools(self) -> list[CallableTool]:
        if not self.provider.enabled:
            return []

        return [
            CallableTool(
                ToolSpec(
                    name='look_at_screen',
                    description=(
                        'Take a screenshot of the user primary monitor and look at it with a local '
                        'multimodal model. Use when the user asks what is on their screen, to read an '
                        'error message they see, or to comment on what they are working on.'
                    ),
                    parameters=(
                        ToolParameter(
                            'question',
                            'string',
                            'What exactly to pay attention to on the screen.',
                            required=False,
                        ),
                    ),
                ),
                lambda call: self._look(str(call.arguments.get('question') or '').strip() or None),
            ),
        ]

    def _look(self, question: str | None) -> ToolResult:
        try:
            with tempfile.TemporaryDirectory(prefix='herta_screen_') as temp_dir:
                shot = self.provider.capture_screen(Path(temp_dir))
                description = self.provider.describe_image(shot, question)
        except Exception as exc:
            logger.warning('Не удалось посмотреть на экран: %s', exc)
            return ToolResult(
                action_name='look_at_screen',
                message=f'Не смогла посмотреть на экран: {exc}',
                executed=False,
            )

        return ToolResult(
            action_name='look_at_screen',
            message=description,
            executed=True,
            data={
                'needs_followup': True,
                'prompt_block': build_vision_context(description, question),
                'question': question or '',
            },
        )
