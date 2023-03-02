# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from datetime import datetime, timedelta
from odoo import models, fields

import logging
_logger = logging.getLogger(__name__)


class NcSyncLog(models.Model):
    _name = 'nc.sync.log'
    _description = 'Nextcloud Sync Log'
    _order = 'create_date, desc'

    name = fields.Char(string="Sync code")
    description = fields.Char()
    date_start = fields.Datetime(string="Start")
    date_end = fields.Datetime(string="End")
    state = fields.Selection([('connecting', 'Connecting'),
                              ('in_progress', 'In Progress'),
                              ('success', 'Success'),
                              ('failed', 'Failed'),
                              ('error', 'Error')])
    next_cloud_url = fields.Char(string="NextCloud URL")
    odoo_url = fields.Char(string="Odoo URL")
    duration = fields.Char()
    line_ids = fields.One2many('nc.sync.log.line', 'log_id')

    def get_time_diff(self, date_from, date_to=False):
        """
        This method checks the time difference between two datetime objects in hours, minutes and seconds
        @param: date_from, datetime
        @param: date_to, datetime
        @return: float, float, float
        """
        if not date_to:
            date_to = datetime.now()
        diff = date_to - date_from
        # Convert the difference to hours, minutes and seconds
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return hours, minutes, seconds

    def check_and_log_users(self, sync_log_id):
        """
        Function to Check and log NextCloud users information.
        :param sync_log_id: single recordset of nc.sync.log model
        :return: List, NextCloud users that are in linked in odoo
        """
        nc_users = self.env['nextcloud.base'].get_users()["ocs"]["data"]["users"]
        nc_user_email = [nc['id'] for nc in nc_users] + [nc['email'] for nc in nc_users]
        odoo_users = self.env['nc.sync.user'].search_read([('sync_calendar', '=', True)])
        stg_users_odoo_not_in_nc = [x for x in odoo_users if x['user_name'] not in nc_user_email]
        stg_users_nc_not_in_odoo = []
        username_list = [o['user_name'].lower() for o in odoo_users]
        for x in nc_users:
            if x['email'] and x['email'].lower() not in username_list:
                if x['displayname'] and x['displayname'].lower() not in username_list:
                    stg_users_nc_not_in_odoo.append(x)
            else:
                stg_users_nc_not_in_odoo.append(x)
        stg_users_nc_in_odoo = []
        # Compare Odoo users with Nextcloud users
        if stg_users_odoo_not_in_nc:
            odoo_usernames = ", ".join([x['name'] for x in stg_users_odoo_not_in_nc if x['name']])
            sync_log_id.line_ids.create({
                'log_id': sync_log_id.id,
                'operation_type': 'read',
                'severity': 'info',
                'response_description': '''Compare Odoo users with Nextcloud users\n\t\tOdoo users not in Nextcloud: %s''' % odoo_usernames,
            })
        # Compare Nextcloud users with Odoo users
        if stg_users_nc_not_in_odoo:
            nc_usernames = ", ".join([x['displayname'] for x in stg_users_nc_not_in_odoo if x['displayname']])
            sync_log_id.line_ids.create({
                'log_id': sync_log_id.id,
                'operation_type': 'read',
                'severity': 'info',
                'response_description': '''Compare Nextcloud users with Odoo users\n\tNextcloud users not in Odoo: %s''' % nc_usernames,
            })
        for odoo_user in odoo_users:
            for nc_user in nc_users:
                user_list = []
                if 'email' in nc_user and nc_user['email']:
                    user_list.append(nc_user['email'].lower())
                if 'id' in nc_user and nc_user['id']:
                    user_list.append(nc_user['id'].lower())
                if odoo_user['user_name'].lower() in user_list:
                    stg_users_nc_in_odoo.append(odoo_user)
                    self.env['nc.sync.user'].browse(odoo_user['id']).write({'nextcloud_user_id': nc_user['id']})

        # Number of  users to sync
        sync_log_id.line_ids.create({
            'log_id': sync_log_id.id,
            'operation_type': 'read',
            'severity': 'info',
            'response_description': '''Number of users to sync: %s''' % len(stg_users_nc_in_odoo),
        })

        return stg_users_nc_in_odoo

    def log_event(self, mode='text', log_id=False, **params):
        """
        This method takes care of the logging process
        @param: mode, string, indicates the sync phase
        @param: log_id, single recordset of nc.sync.log model
        @return dictionary of values
        """
        result = {'resume': True, 'stg_users_nc_in_odoo': []}
        config_obj = self.env['ir.config_parameter']
        log_line = self.env['nc.sync.log.line']

        nc_url = config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_url') + '/remote.php/dav'
        odoo_url = config_obj.sudo().get_param('web.base.url')
        username = config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_login')
        password = config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_password')

        caldav_sync = config_obj.sudo().get_param('nextcloud_odoo_sync.enable_calendar_sync')
        caldav_obj = self.env['nextcloud.caldav']
        if mode == 'pre_sync':
            # Start Sync Process: Date + Time
            log_id = self.env['nc.sync.log'].create({
                'name': datetime.now().strftime('%Y%m%d-%H%M%S'),
                'date_start': datetime.now(),
                'state': 'connecting',
                'next_cloud_url': nc_url,
                'odoo_url': odoo_url,
                'line_ids': [(0, 0, {'operation_type': 'login',
                                     'response_description': 'Start Sync Process'})],
            })
            result['log_id'] = log_id
            # Nextcloud connection test for Caldav
            if caldav_sync:
                res = {'operation_type': 'login',
                       'log_id': log_id.id,
                       'data_send': 'url: %s, username: %s, password: *****' % (nc_url, username)}
                connection, connection_principal = caldav_obj.check_nextcloud_connection(url=nc_url, username=username, password=password)
                if not isinstance(connection_principal, dict):
                    res['response_description'] = 'Nextcloud connection test for Caldav: OK'
                else:
                    res['response_description'] = '''Nextcloud connection test for Caldav: Error \n\t%s''' % connection_principal['response_description']
                    res['error_code_id'] = connection_principal['sync_error_id'].id if 'sync_error_id' in connection_principal else False
                    result['resume'] = False
                log_line.create(res)

            # Compare Nextcloud users with Odoo users and vice versa
            if result['resume'] and log_id:
                result['stg_users_nc_in_odoo'] = self.check_and_log_users(log_id)

        else:
            error = str(params['error']) if 'error' in params else False
            if not log_id:
                log_id = self.browse(self.ids[0])
            res = {'log_id': log_id.id,
                   'operation_type': params['operation_type'] if 'operation_type' in params else 'read',
                   'severity': params['severity'] if 'severity' in params else 'info', }

            if mode == 'text' and 'message' in params:
                res['response_description'] = params['message']

            elif mode == 'error' and error:
                # Undo the last uncommitted changes
                self.env.cr.rollback()
                message = '%s ' % params['message'] if 'message' in params else ''
                res['response_description'] = '''%s%s''' % (message, error)

            try:
                log_line.create(res)
            except Exception as e:
                _logger.warning('Error encountered during log operation: %s' % e)
                return result

        # Create an event log
        if 'response_description' in res:
            _logger.warning(res['response_description'])
        # Commit the changes to the database
        self.env.cr.commit()
        return result


class NcSyncLogLine(models.Model):
    _name = 'nc.sync.log.line'
    _description = 'Nextcloud Sync Log Line'

    log_id = fields.Many2one('nc.sync.log', ondelete='cascade')
    operation_type = fields.Selection([('create', 'Create'),
                                       ('write', 'Write'),
                                       ('delete', 'Delete'),
                                       ('read', 'Read'),
                                       ('login', 'Login'),
                                       ('conflict', 'Conflict'),
                                       ('warning', 'Warning'),
                                       ('error', 'Error')])
    prev_value = fields.Text(string="Previous Value")
    new_value = fields.Text()
    data_send = fields.Text()
    error_code_id = fields.Many2one('nc.sync.error')
    severity = fields.Selection([('debug', 'Debug'),
                                 ('info', 'Info'),
                                 ('warning', 'Warning'),
                                 ('error', 'Error'),
                                 ('critical', 'Critical')], default='info')
    response_description = fields.Text()
