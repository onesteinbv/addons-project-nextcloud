# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import api, models, fields, _
from odoo.exceptions import ValidationError


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    def _get_nc_calendar_selection(self):
        nc_calendar_ids = self.env['nc.calendar'].search([('user_id', '=', self.env.user.id)])
        values = []
        if nc_calendar_ids:
            [values.append((x.id, x.name)) for x in nc_calendar_ids]
        return values

    nc_uid = fields.Char(string="UID")
    nc_calendar_hash = fields.Char(string="Calendar Hash")
    nc_color = fields.Char(string="Color")
    nc_calendar_id = fields.Many2one('nc.calendar', 'Nextcloud Calendar', compute='_compute_nc_calendar')
    nc_calendar_select = fields.Selection(_get_nc_calendar_selection, string='Nextcloud Calendar',
                                          help='Select which Nextcloud Calendar the event will be recorded into')
    nc_status = fields.Many2one('nc.event.status', string="Status")
    nc_resources = fields.Many2one('resource.resource', string="Resources")
    nc_calendar_ids = fields.Many2many('nc.calendar', string='Calendars')
    nc_require_calendar = fields.Boolean(compute='_compute_nc_require_calendar')
    nc_synced = fields.Boolean(string="Synced")
    nc_to_delete = fields.Boolean(string="To Delete")
    nc_allday = fields.Boolean(string="Nextcloud All day")

    @api.model
    def default_get(self, fields):
        res = super(CalendarEvent, self).default_get(fields)
        res['privacy'] = 'private'
        return res

    @api.depends('duration', 'partner_ids')
    def _compute_nc_require_calendar(self):
        """
        This method determine whether to require a value for the Nextcloud calendar
        """
        nc_calendar_ids = self.env['nc.calendar'].search([('user_id', '=', self.env.user.id)])
        if nc_calendar_ids:
            self.nc_require_calendar = True
        else:
            self.nc_require_calendar = False

    @api.depends('nc_calendar_ids')
    def _compute_nc_calendar(self):
        """
        This method computes the value of the Nextcloud calendar name to display
        """
        # TODO: Handle calendar event with privacy == 'private' without using sudo()
        for event in self.sudo().with_context(sync=True):
            calendar = calendar_id = False
            if event.nc_calendar_ids:
                # Get calendar to display on the event based on the current user
                calendar_id = event.nc_calendar_ids.filtered(lambda x: x.user_id == self.env.user)
                if calendar_id:
                    calendar = calendar_id.ids[0]
            event.nc_calendar_id = calendar
            event.nc_calendar_select = calendar_id.ids[0] if calendar_id else False

    @api.onchange('user_id')
    def onchange_nc_user_id(self):
        if self.user_id:
            default_calendar_id = self.env['nc.sync.user'].search([('user_id', '=', self.user_id.id)], limit=1).mapped('nc_calendar_id')
            if default_calendar_id:
                self.nc_calendar_select = default_calendar_id.id

    @api.onchange('nc_calendar_select')
    def onchange_nc_calendar_select(self):
        if self.nc_calendar_select:
            calendar_id = self.env['nc.calendar'].browse(int(self.nc_calendar_select))
            new_calendar_ids = []
            if self.nc_calendar_ids:
                new_calendar_ids = self.nc_calendar_ids.ids
                # Get previously linked current user calendar and replace it with the newly selected calendar
                prev_calendar_ids = self.nc_calendar_ids.filtered(lambda x: x.user_id == self.env.user)
                if prev_calendar_ids:
                    new_calendar_ids = list(set(new_calendar_ids) - set(prev_calendar_ids.ids))
            if calendar_id:
                new_calendar_ids.append(calendar_id.id)
                self.nc_calendar_ids = [(6, 0, new_calendar_ids)]

    @api.constrains('user_id', 'name', 'start', 'stop', 'start_date', 'stop_date')
    def check_duplicate(self):
        for event in self:
            fields = ['name', 'start', 'stop', 'start_date', 'stop_date']
            domain = [('id', '!=', event.id), ('user_id', '=', event.user_id.id)]
            [domain.append(('%s' % key, '=', event[key])) for key in fields]
            duplicate_id = self.search(domain)
            if duplicate_id:
                raise ValidationError(_('An existing event named %s with the same date and attendees already exist' % event.name))

    @api.model
    def create(self, vals):
        if 'allday' in vals:
            vals['nc_allday'] = vals['allday']
        # Set default calendar event to private
        if 'privacy' not in vals or not vals['privacy']:
            vals['privacy'] = 'private'
        res = super(CalendarEvent, self).create(vals)
        if 'nc_calendar_ids' not in vals:
            # Check if a value for calendar exist for the user:
            nc_sync_user_id = self.env['nc.sync.user'].search([('user_id', '=', self.env.user.id)], limit=1)
            if nc_sync_user_id and nc_sync_user_id.nc_calendar_id:
                res.nc_calendar_ids = [(4, nc_sync_user_id.nc_calendar_id.id)]
        if 'nc_status' not in vals:
            vals['nc_status'] = self.env.ref('nextcloud_odoo_sync.nc_event_status_confirmed').id
        return res

    def write(self, vals):
        if not self._context.get('sync', False):
            vals['nc_synced'] = False
        return super(CalendarEvent, self).write(vals)

    def unlink(self):
        for record in self:
            if not self._context.get('sync', False) and record.nc_require_calendar and not self.env.context.get('force_delete', False):
                record.write({'nc_to_delete': True})
            else:
                return super(CalendarEvent, self).unlink()
