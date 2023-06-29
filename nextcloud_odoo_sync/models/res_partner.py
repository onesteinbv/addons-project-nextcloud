# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields


class ResPartner(models.Model):
    _inherit = "res.partner"

    nc_sync = fields.Boolean()
