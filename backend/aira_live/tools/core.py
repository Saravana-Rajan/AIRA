"""Core Live tools. OWNER: Saravana."""
from typing import Literal

from aira_live import confirm


async def confirm_action(confirm_id: str, answer: Literal["yes", "no"]) -> dict:
    """Resolve a pending confirmation after the shop OWNER clearly says yes or no.

    Call this only with the confirm_id returned by a tool that said needs_confirm, and only for the owner's
    answer (never a customer's). If the answer is unclear, ask again instead of guessing.
    """
    return await confirm.resolve(confirm_id, answer, None)
