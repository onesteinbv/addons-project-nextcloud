# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields


class CalendarRecurrence(models.Model):
    _inherit = "calendar.recurrence"

    nc_exdate = fields.Char("Nextcloud Exdate")
