"""Core Live tools. OWNER: Saravana."""
from typing import Literal

from aira_live import confirm


async def confirm_action(confirm_id: str, answer: Literal["yes", "no"]) -> dict:
    """Resolve a pending proposal after the owner says yes or no out loud. Use the confirm_id from the proposal."""
    return await confirm.resolve(confirm_id, answer, None)
