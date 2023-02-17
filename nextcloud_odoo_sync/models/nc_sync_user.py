# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class NcSyncUser(models.Model):
    _name = 'nc.sync.user'

    user_id = fields.Many2one('res.users', 'Odoo User', default=lambda self: self.env.user.id)
    name = fields.Char('Odoo Username', related='user_id.name')
    nextcloud_user_id = fields.Char('Nextcloud User ID')
    user_name = fields.Char('Username')
    sync_calendar = fields.Boolean('Sync Calendar', default=True)
    user_events_hash = fields.Text('Event Hash')
    nc_password = fields.Char('Password')
    nc_calendar_id = fields.Many2one('nc.calendar', 'Default Nextcloud Calendar')
    user_has_calendar = fields.Boolean('User has calendar')
    user_message = fields.Char(default='"Default Calendar" field will be used as your default odoo calendar when creating new events')

    @api.constrains('user_id')
    def check_user_exist(self):
        for user in self:
            sync_user_id = self.search(['&', ('id', '!=', user.id), '|', ('user_id', '=', user.user_id.id), ('user_name', '=ilike', user.user_name)], limit=1)
            if sync_user_id:
                raise ValidationError(_('Existing configuration found. The selected Odoo User "%s" or Nextcloud Username "%s" is already mapped to an existing record' % (user.user_id.name, user.user_name)))

    @api.onchange('nc_calendar_id')
    def onchange_nc_calendar_id(self):
        if self.nc_calendar_id:
            self.user_message = '%s will be used as your default Odoo calendar when creating new events' % self.nc_calendar_id.name
        else:
            self.user_message = '"Default Calendar" field will be used as your default odoo calendar when creating new events'

    def save_user_config(self):
        """
        This method will apply the default calendar selected for all existing Odoo events of the user
        that has no nc_calendar_id value. This will be used to identify which Nextcloud Calendar
        will be use during the initial sync operation
        """
        return self.env.ref('calendar.action_calendar_event').sudo().read()[0]

    def write(self, vals):
        if 'nc_calendar_id' in vals:
            calendar_event_ids = self.env['calendar.event'].search(
                ['|', ('user_id', '=', self.user_id.id), ('partner_ids', 'in', self.user_id.partner_id.id)]
            ).filtered(lambda x: not x.nc_calendar_ids or (x.nc_calendar_ids and self.user_id.partner_id.id not in x.nc_calendar_ids.ids))
            calendar_event_ids.with_context(sync=True).write({'nc_calendar_ids': [(4, vals['nc_calendar_id'])]})
        return super(NcSyncUser, self).write(vals)

    def get_user_connection(self):
        params = {'nextcloud_login': 'Login', 'nextcloud_password': 'Password', 'nextcloud_url': 'Server URL'}
        for item in params:
            value = self.env['ir.config_parameter'].sudo().get_param('nextcloud_odoo_sync.%s' % item)
            if not value:
                raise ValidationError(_('Missing value for "%s" field in Settings/ Nextcloud' % params[item]))

        nc_url = self.env['ir.config_parameter'].sudo().get_param('nextcloud_odoo_sync.nextcloud_url') + '/remote.php/dav'
        connection, connection_principal = self.env['nextcloud.caldav'].check_nextcloud_connection(url=nc_url, username=self.user_name, password=self.nc_password)
        if isinstance(connection_principal, dict):
            raise ValidationError(f"{connection_principal['sync_error_id'].name}: {connection_principal['response_description']}")
        return connection, connection_principal

    def get_user_calendars(self, connection):
        nc_calendars = connection.calendars()
        result = []
        for record in nc_calendars:
            nc_calendar_id = self.nc_calendar_id.search([('user_id', '=', self.user_id.id), ('name', '=', record.name), ('calendar_url', '=', record.canonical_url)], limit=1)
            if not nc_calendar_id:
                result.append({
                    'name': record.name,
                    'user_id': self.user_id.id,
                    'calendar_url': record.canonical_url
                })
        if result:
            self.user_has_calendar = True
            self.nc_calendar_id.create(result)
        calendar_not_in_odoo_ids = self.nc_calendar_id.search([('calendar_url', 'not in', [str(x.canonical_url) for x in nc_calendars]), ('user_id', '=', self.user_id.id)])
        if calendar_not_in_odoo_ids:
            calendar_not_in_odoo_ids.unlink()

    def check_nc_connection(self):
        connection, connection_principal = self.sudo().get_user_connection()
        self.sudo().get_user_calendars(connection_principal)
        return {
            'name': 'NextCloud User Setup',
            'view_mode': 'form',
            'res_model': 'nc.sync.user',
            'view_id': self.env.ref('nextcloud_odoo_sync.nc_sync_user_form_view').id,
            'type': 'ir.actions.act_window',
            'context': {'pop_up': True},
            'target': 'new',
            'res_id': self.id
        }

    def unlink(self):
        if self.user_id:
            nc_calendar_ids = self.env['nc.calendar'].search([('user_id', 'in', self.user_id.ids)])
            if nc_calendar_ids:
                nc_calendar_ids.unlink()
        return super(NcSyncUser, self).unlink()
