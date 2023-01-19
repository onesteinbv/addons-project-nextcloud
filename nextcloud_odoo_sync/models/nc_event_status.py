# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import api, models, fields


class NcEventStatus(models.Model):
    _name = 'nc.event.status'

    name = fields.Char()
