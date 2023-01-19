# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields


class NcsyncUser(models.Model):
    _name = 'nc.sync.user'

    user_id = fields.Many2one('res.users')
    name = fields.Char(related='user_id.name')
    nextcloud_user_id = fields.Char()
    user_name = fields.Char()
    sync_calendar = fields.Boolean(default=True)
