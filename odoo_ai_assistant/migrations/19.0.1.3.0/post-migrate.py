"""Stop counting earlier AI replies as unread Discuss chat messages.

New replies are marked read when they are posted; this does the same once for
the replies already stored, so the Discuss chat counter no longer includes them.
The AI panel keeps its own unread state, which this does not change.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["odoo.ai.assistant.conversation"].search([])._mark_channel_read()
