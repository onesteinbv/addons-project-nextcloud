# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import api, models, fields


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    nc_uid = fields.Char()
    nc_calendar_hash = fields.Char()
    nc_status = fields.Many2one('nc.event.status')
    nc_color = fields.Char()
    nc_resources = fields.Many2one('resource.resource')
    nc_calendar_ids = fields.Many2many('nc.calendar')
    nc_synced = fields.Boolean()
    
    def write(self, vals):
        if not self._context.get('sync', False):
            vals['nc_synced'] = False
        return super(CalendarEvent, self).write(vals)