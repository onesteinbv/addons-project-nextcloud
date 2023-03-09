# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import ast
from odoo import api, models, fields


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    def _get_nc_calendar_selection(self):
        nc_calendar_ids = self.env['nc.calendar'].search([('user_id', '=', self.env.user.id)])
        values = []
        if nc_calendar_ids:
            [values.append((x.id, x.name)) for x in nc_calendar_ids]
        return values

    nc_uid = fields.Char('UID')
    nc_rid = fields.Char('RECURRENCE-ID', compute='_compute_nc_rid', store=True)
    nc_color = fields.Char(string="Color")

    nc_calendar_select = fields.Selection(_get_nc_calendar_selection, string='Nextcloud Calendar',
                                          help='Select which Nextcloud Calendar the event will be recorded into')

    nc_calendar_id = fields.Many2one('nc.calendar', 'Nextcloud Calendar', compute='_compute_nc_calendar')
    nc_status = fields.Many2one('nc.event.status', string="Status")
    nc_resources = fields.Many2one('resource.resource', string="Resources")
    nc_calendar_ids = fields.Many2many('nc.calendar', string='Calendars')
    nc_hash_ids = fields.One2many('calendar.event.nchash', 'calendar_event_id', 'Hash Values')

    nc_require_calendar = fields.Boolean(compute='_compute_nc_require_calendar')
    nc_synced = fields.Boolean('Synced')
    nc_to_delete = fields.Boolean('To Delete')
    nc_allday = fields.Boolean('Nextcloud All day')
    nc_detach = fields.Boolean('Detach from recurring event')

    @api.model
    def default_get(self, fields):
        res = super(CalendarEvent, self).default_get(fields)
        res['nc_status'] = self.env.ref('nextcloud_odoo_sync.nc_event_status_confirmed').id
        res['privacy'] = 'private'
        return res

    @api.depends('recurrence_id')
    def _compute_nc_rid(self):
        """
        This method generates a value for RECURRENCE-ID of Nextcloud recurring event
        """
        for event in self:
            if event.recurrence_id and not event.nc_rid:
                if not event.allday:
                    event.nc_rid = event.start.strftime('%Y%m%dT%H%M%S')
                else:
                    event.nc_rid = event.start.strftime('%Y%m%d')
            else:
                event.nc_rid = False

    @api.depends('duration', 'partner_ids', 'user_id')
    def _compute_nc_require_calendar(self):
        """
        This method determine whether to require a value for the Nextcloud calendar
        """
        nc_calendar_ids = self.env['nc.calendar'].search([('user_id', '=', self.env.user.id)])
        for event in self:
            if nc_calendar_ids and event.user_id and event.user_id == self.env.user:
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
            if self.nc_require_calendar:
                default_calendar_id = self.env['nc.sync.user'].search([('user_id', '=', self.user_id.id)], limit=1).mapped('nc_calendar_id')
                if default_calendar_id and self.user_id == self.env.user:
                    self.nc_calendar_select = default_calendar_id.id
            else:
                self.nc_calendar_select = False
                self.nc_calendar_ids = False

    @api.onchange('nc_calendar_select')
    def onchange_nc_calendar_select(self):
        if self.nc_calendar_select and self.nc_require_calendar:
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

    def check_duplicate(self, user_id, vals, events):
        """
        This method is use to check if duplicate events exist
        :param user_id, integer, record id of the user in res.users model
        :param vals, dictionary, event values to create
        :param events, recordset of calendar.event
        """
        fields = ['name', 'start', 'stop', 'start_date', 'stop_date']
        partner_id = self.env['res.users'].browse(user_id).partner_id
        duplicate = False
        for event in events:
            event = self.search([('id', '=', event.id), ('nc_to_delete', '=', True)])
            if event:
                duplicate = True
                for field in fields:
                    if field in vals and field in event and vals[field] and event[field] and vals[field] != event[field]:
                        duplicate = False
                        break
                if duplicate and event.partner_ids and partner_id.id not in event.partner_ids.ids:
                    duplicate = False
        return duplicate

    @api.model
    def create(self, vals):
        # Handle untitled event since Nextcloud event can be saved without title
        if 'name' not in vals or not vals['name']:
            vals['name'] = 'Untitled event'
        if 'allday' in vals:
            vals['nc_allday'] = vals['allday']
        # Set default calendar event to private. Nextcloud event are not viewable by Nextcloud admin
        # unlike the case with Odoo, so we need to mimic the same functionality
        if 'privacy' not in vals or not vals['privacy']:
            vals['privacy'] = 'private'
        if 'nc_status' not in vals or not vals['nc_status']:
            vals['nc_status'] = self.env.ref('nextcloud_odoo_sync.nc_event_status_confirmed').id
        res = super(CalendarEvent, self).create(vals)
        if 'nc_calendar_ids' not in vals or vals['nc_calendar_ids'] == [[6, False, []]]:
            # Check if a value for calendar exist for the user:
            nc_sync_user_id = self.env['nc.sync.user'].search([('user_id', '=', vals['user_id'])], limit=1)
            if nc_sync_user_id and nc_sync_user_id.nc_calendar_id:
                res.nc_calendar_ids = [(4, nc_sync_user_id.nc_calendar_id.id)]
        return res

    def write(self, vals):
        if not self._context.get('sync', False) and 'nc_synced' not in vals:
            vals['nc_synced'] = False
        for record in self:
            # Detach the record from recurring event whenever an edit was made to make
            # it compatible when synced to Nextcloud calendar
            if record.recurrence_id:
                detach = False
                fields_to_update = list(vals.keys())
                ex_fields = ['nc_uid', 'nc_rid', 'nc_hash_ids', 'nc_synced', 'nc_to_delete', 'recurrence_id', 'nc_calendar_select']
                for f in fields_to_update:
                    if f not in ex_fields:
                        detach = True
                        break
                if detach:
                    vals.update({'nc_detach': True})
        return super(CalendarEvent, self).write(vals)

    def unlink(self):
        """
        We can't delete an event that is also in Nextcloud Calendar. Otherwise we would
        have no clue that the event must must deleted from Nextcloud Calendar at the next sync.
        We just mark the event as to delete (nc_to_delete=True) before we sync.
        """
        for record in self:
            if record.nc_uid and not self._context.get('force_delete', False):
                record.write({'nc_to_delete': True})
            else:
                if record.recurrence_id:
                    nc_exdates = ast.literal_eval(str(record.recurrence_id.nc_exdate)) if record.recurrence_id.nc_exdate else []
                    start_date = record.start.strftime('%Y%m%dT%H%M%S')
                    if record.allday:
                        start_date = record.start_date.strftime('%Y%m%d')
                    nc_exdates.append(start_date)
                    record.recurrence_id.write({'nc_exdate': nc_exdates})
                return super(CalendarEvent, self).unlink()


class CalendarEventNchash(models.Model):
    _name = 'calendar.event.nchash'
    _description = 'Calendar Event Nextcloud Hash'

    calendar_event_id = fields.Many2one('calendar.event', 'Calendar Event', ondelete='cascade')
    user_id = fields.Many2one('res.users', 'Odoo User', related='nc_sync_user_id.user_id', store=True)
    nc_sync_user_id = fields.Many2one('nc.sync.user', 'Sync User', ondelete='cascade')
    nc_uid = fields.Char('UID', related='calendar_event_id.nc_uid', store=True)
    nc_event_hash = fields.Char('Event Hash')

    def create(self, vals):
        return super(CalendarEventNchash, self).create(vals)
