"""Language detection only: ``detect_lang``, the sticky ``follow_lang`` and the shop's expected languages.

AIRA has no default and no fixed language, and no fixed sentences: tools return facts and Gemini Live says them
in whatever language the owner is speaking. This package only tells which language a transcript is in
(``detect``) and which languages a shop usually speaks (``expected``). The phone's own fallback lines, for when
Live cannot speak, live in ``aira_live.system_lines``.
"""
from aira_live.i18n.detect import detect_lang, follow_lang

__all__ = ["detect_lang", "follow_lang"]
