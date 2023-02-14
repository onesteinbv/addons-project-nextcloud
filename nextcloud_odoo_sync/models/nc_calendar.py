# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields, api


class NcsyncUser(models.Model):
    _name = 'nc.calendar'

    name = fields.Char(string='Calendar')
    user_id = fields.Many2one('res.users', string='User', ondelete='cascade')
    calendar_url = fields.Text(string='Calendar URL')

    @api.model
    def create(self, vals):
        if not self._context.get('sync', False):
            vals['user_id'] = self.env.user.id
        return super(NcsyncUser, self).create(vals)
