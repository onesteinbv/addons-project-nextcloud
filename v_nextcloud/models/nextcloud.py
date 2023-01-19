# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import pytz
import logging

from dateutil.parser import parse
import time as ttime
from datetime import datetime, timedelta
from odoo import api, models, fields, tools, SUPERUSER_ID, _
from odoo.http import request
from odoo.addons.nextcloud_odoo_sync.models import jicson

_logger = logging.getLogger(__name__)

try:
    import caldav

except (ImportError, IOError) as err:
    _logger.debug(err)

EVENT_COUNT = 1000  # Indicate the number of event to create


class NextcloudSync(models.Model):
    _name = 'nextcloud.sync'
    _inherit = ['nextcloud.base']
    _description = 'Next Cloud Sync'

    name = fields.Char('Name', required=True)
    api_url = fields.Char('API URL', default='/ocs/v1.php/cloud/users')
    hostname = fields.Char('Hostname', default='https://nctest.volendra.net')
    username = fields.Char('Username', default='admin')
    password = fields.Char('Password', default='D3f@ult101')
    json_output = fields.Boolean('JSON Output')
    calendar_event_id = fields.Many2one('calendar.event')
    event_count = fields.Integer('Event Count', default=10)
    event_count_display = fields.Integer(related='event_count')

    result_log = fields.Text('Result Log')

    def sync_cron_test(self):
        for rec in self:
            start_time = ttime.perf_counter()
            config_obj = rec.env['ir.config_parameter']
            caldav_api_credentials = {
                'url': config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_url') + '/remote.php/dav',
                'username': config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_login'),
                'pw': config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_password'),
                'enabled': config_obj.sudo().get_param('nextcloud_odoo_sync.enable_calendar_sync')
            }
            sync_log_id = rec.env['nc.sync.log'].create({
                'name': datetime.now().strftime('%Y%m%d-%H%M%S'),
                'date_start': datetime.now(),
                'state': 'connecting',
                'next_cloud_url': caldav_api_credentials['url'],
                'odoo_url': self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            })
            rec.env.cr.commit()
            
            caldav_api_credentials_copy = caldav_api_credentials.copy()
            caldav_api_credentials_copy.update({'pw': '*****'})
            connection = False
            if caldav_api_credentials['enabled']:
                connection = rec.env['nextcloud.caldav'].check_nextcloud_connection(url=caldav_api_credentials['url'], username=caldav_api_credentials['username'], password=caldav_api_credentials['pw'])

                sync_log_id.line_ids.create({
                    'log_id': sync_log_id.id,
                    'operation_type': 'login',
                    'data_send': caldav_api_credentials_copy,
                    'response_description': connection.get('response_description') if isinstance(connection, dict) else str(connection),
                    'error_code_id': connection.get('sync_error_id').id if isinstance(connection, dict) else False,
                    'severity': connection.get('sync_error_id').severity if isinstance(connection, dict) else 'info'
                })
            else:
                sync_log_id.line_ids.create({
                    'log_id': sync_log_id.id,
                    'operation_type': 'login',
                    'data_send': caldav_api_credentials_copy,
                    'response_description': 'Calendar Sync not enabled',
                    'error_code_id': False,
                    'severity': 'info'
                })
            
            if isinstance(connection, dict) and connection.get('sync_error_id'):
                sync_log_id.write({'state': 'failed', 'date_end': datetime.now()})
            else:
                sync_log_id.write({'state': 'ok', 'date_end': datetime.now()})
            
            errors = sync_log_id.line_ids.filtered(lambda x: x.severity in ['error', 'critical'])
            warnings = sync_log_id.line_ids.filtered(lambda x: x.severity in ['warning'])
            infos = sync_log_id.line_ids.filtered(lambda x: not x.severity or x.severity in ['info'])
            sync_log_id.description = f'{len(errors)} Error(s), {len(warnings)} Warning(s) and {len(infos)} Info(s)'
            
            end_time = ttime.perf_counter()
            elapsed = end_time - start_time
            duration = round(elapsed, 2)
            sync_log_id.duration = self.convert_readable_time_duration(duration)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': ('Nextcloud Sync'),
                    'message': 'Sync Done',
                    'type': 'success',
                    'sticky': False,
                },
            }

    def test_webdav(self):
        """
        This method authenticates to NextCloud server using the provided users
        """
        for rec in self:
            res = rec.get_users()
            rec.result_log = res

    def test_caldav(self):
        for rec in self:
            rec.env['nextcloud.caldav'].check_nextcloud_connection(url=rec.hostname + '/remote.php/dav', username=rec.username, password=rec.password)

            with caldav.DAVClient(url=rec.hostname + '/remote.php/dav', username=rec.username, password=rec.password) as client:
                my_principal = client.principal()
                calendars = my_principal.calendars()
                result = []
                for calendar in calendars:
                    calendar_events = {'Calendar name': calendar.name, 'Events': []}
                    for event in calendar.events():
                        vcalendar = jicson.fromText(event.data)
                        calendar_events['Events'].extend(vcalendar.get('VCALENDAR'))
                    result.append(calendar_events)
                rec.result_log = result

            # nextcloud_caldav_obj = self.env['nextcloud.caldav']
            # rec.result_log = nextcloud_caldav_obj.get_caldav_event(1, self.get_local_datetime(datetime(2022, 12, 11)), self.get_local_datetime(datetime(2022, 12, 13)), 'Personal')
            # nextcloud_caldav_obj.get_caldav_record(False)

    def get_local_datetime(self, datetime_datetime):
        user_tz = self.env.user.tz or pytz.utc
        local = pytz.timezone(user_tz)
        return datetime_datetime.astimezone(local)

    def create_next_cloud_event(self, calendar_events):
        for rec in self:
            nextcloud_caldav_obj = self.env['nextcloud.caldav']
            calendar_event_ids = nextcloud_caldav_obj.set_caldav_record(calendar_events)
            
            with caldav.DAVClient(url=rec.hostname + '/remote.php/dav', username=rec.username, password=rec.password) as client:
                my_principal = client.principal()
                calendar_obj = my_principal.calendar(name="Personal")
                for event_info in calendar_event_ids:
                    event = {}
                    for info in event_info:
                        if info in ['summary', 'dtstart', 'dtend']:
                            event[info] = event_info[info]
                    try:
                        event_url = calendar_obj.save_event(**event)
                        print("Event created successfully:", event_url)
                    except Exception as e:
                        print("Error creating event:", e)
                        continue

    def create_odoo_event(self):
        for rec in self:
            google_service = rec.env['google.service']
            spreadsheet_id = google_service.execute_google_process('PARSE_URL', {'url': 'https://docs.google.com/spreadsheets/d/10PIYdcxet2kya2ZNvebyMHRpcnh2fBpPS52z4uexPFc/edit#gid=0'})
            params = {
                'sheet_id': spreadsheet_id.get('spreadsheet_id'),
                'sheet_range': "A2:H",
            }
            data_result = google_service.sudo().execute_google_process('READ', params)
            params['data_result'] = data_result
            result = google_service.sudo().execute_google_process('GET_LISTED_DICT', params)
            for item in result:
                for data in item:
                    if data in ['start', 'stop']:
                        item[data] = parse(item[data])
                    elif data == 'partner_ids' and item[data]:
                        partner_ids = rec.env['res.partner'].search([('email', 'in', item[data].replace("\n", "").split(','))])
                        item[data] = [(6, 0, partner_ids.ids)]
                    elif data == 'categ_ids' and item[data]:
                        categ_ids = rec.env['calendar.event.type'].search([('name', 'in', item[data].replace("\n", "").split(','))])
                        item[data] = [(6, 0, categ_ids.ids)]
            rec.env['calendar.event'].create(result)

    def test_carddav(self):
        return True

    def create_odoo_events(self):

        datetime_opration_start = datetime.now()
        start_time = ttime.time()
        start_datetime = datetime(2023, 1, 1)
        end_datetime = datetime(2023, 1, 1) + timedelta(hours=1)
        result = []
        for n in range(1, self.event_count + 1):
            vals = {
                'name': f'Event {n}',
                'start_date': str(start_datetime),
                'stop_date': str(end_datetime),
                'allday': False
            }
            result.append(vals)
            start_datetime = start_datetime + timedelta(hours=1)
            end_datetime = end_datetime + timedelta(hours=1)
        self.calendar_event_id.create(result)
        end_time = ttime.time()
        elapsed = end_time - start_time
        duration = round(elapsed, 2)
        duration = self.convert_readable_time_duration(duration)
        self.result_log = f"Created {n} Odoo event records\nStart Time: {datetime_opration_start.strftime('%Y-%m-%d %H:%M:%S')}\nEnd Time: {str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\nDuration: {duration}"

    def create_nextcloud_events(self):
        for rec in self:
            datetime_opration_start = datetime.now()
            start_time = ttime.time()

            nextcloud_caldav_obj = self.env['nextcloud.caldav']
            calendar_event_ids = rec.calendar_event_id.search([], limit=rec.event_count)
            calendar_event_ids = nextcloud_caldav_obj.set_caldav_record(calendar_event_ids)

            with caldav.DAVClient(url=rec.hostname + '/remote.php/dav', username=rec.username, password=rec.password) as client:
                my_principal = client.principal()
                calendar_obj = my_principal.calendar(name="Personal")
                for event_info in calendar_event_ids:
                    event = {}
                    for info in event_info:
                        if info in ['summary', 'dtstart', 'dtend']:
                            event[info] = event_info[info]
                    try:
                        event_url = calendar_obj.save_event(**event)
                        print("Event created successfully:", event_url)
                    except Exception as e:
                        print("Error creating event:", e)
                        continue

            end_time = ttime.time()
            elapsed = end_time - start_time
            duration = round(elapsed, 2)
            duration = rec.convert_readable_time_duration(duration)
            self.result_log = f"Created {len(calendar_event_ids)} Nextcloud event records\nStart Time: {datetime_opration_start.strftime('%Y-%m-%d %H:%M:%S')}\nEnd Time: {str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\nDuration: {duration}"

    def load_all_nextcloud_events(self):
        for rec in self:
            datetime_opration_start = datetime.now()
            start_time = ttime.time()

            nextcloud_caldav_obj = rec.env['nextcloud.caldav']
            events = nextcloud_caldav_obj.get_caldav_event(False, datetime(2023, 1, 13), datetime(2023, 1, 13, 23), "Personal")

            end_time = ttime.time()
            elapsed = end_time - start_time
            duration = round(elapsed, 2)
            duration = rec.convert_readable_time_duration(duration)
            self.result_log = f"Loaded {len(events)} Nextcloud event records\nStart Time: {datetime_opration_start.strftime('%Y-%m-%d %H:%M:%S')}\nEnd Time: {str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\nDuration: {duration}"

    def delete_all_nextcloud_events(self):
        for rec in self:
            datetime_opration_start = datetime.now()
            start_time = ttime.time()

            with caldav.DAVClient(url=rec.hostname + '/remote.php/dav', username=rec.username, password=rec.password) as client:
                my_principal = client.principal()
                calendar_obj = my_principal.calendar(name="Personal")
                event = calendar_obj.events()
                for event_info in event:
                    event_info.delete()

            end_time = ttime.time()
            elapsed = end_time - start_time
            duration = round(elapsed, 2)
            duration = rec.convert_readable_time_duration(duration)
            self.result_log = f"Deleted all Nextcloud event records\nStart Time: {datetime_opration_start.strftime('%Y-%m-%d %H:%M:%S')}\nEnd Time: {str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\nDuration: {duration}"

    def delete_all_odoo_events(self):
        for rec in self:
            datetime_opration_start = datetime.now()
            start_time = ttime.time()

            rec.calendar_event_id.search([], limit=rec.event_count).write({'active': False})

            end_time = ttime.time()
            elapsed = end_time - start_time
            duration = round(elapsed, 2)
            duration = rec.convert_readable_time_duration(duration)
            self.result_log = f"Deleted all Odoo event records\nStart Time: {datetime_opration_start.strftime('%Y-%m-%d %H:%M:%S')}\nEnd Time: {str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\nDuration: {duration}"

    def convert_readable_time_duration(self, total_time):
        td_str = str(timedelta(seconds=total_time))
        x = td_str.split(':')
        return f"{x[1]} minutes {round(float(x[2]))} seconds" if x[1] != '00' else f"{round(float(x[2]))} Seconds"
