"""Archive the AI's contact on existing databases.

Its data record is ``noupdate``, so the new ``active`` value only reaches fresh
installs. Archived like OdooBot, it keeps authoring AI replies but no longer
appears in contact lists, searches and mentions.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.ref("odoo_ai_assistant.ai_partner").active = False
