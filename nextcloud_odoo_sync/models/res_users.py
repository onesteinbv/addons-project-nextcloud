# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models


class ResUsers(models.Model):
    _inherit = 'res.users'

    def setup_nc_sync_user(self):
        action = {
            'name': 'Nextcloud User Setup',
            'view_mode': 'form',
            'res_model': 'nc.sync.user',
            'type': 'ir.actions.act_window',
            'context': {'pop_up': True},
            'target': 'new'
        }
        nc_sync_user_id = self.env['nc.sync.user'].search([('user_id', '=', self.env.user.id)], limit=1)
        if nc_sync_user_id:
            action['res_id'] = nc_sync_user_id.id
        return action
