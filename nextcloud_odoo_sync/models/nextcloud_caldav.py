# -*- coding: utf-8 -*-
# Copyright (c) 2022 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import logging
import requests
import pytz
import time as ttime
import hashlib
import ast
from dateutil.parser import parse
from datetime import datetime, timedelta
from odoo import models
from odoo.addons.nextcloud_odoo_sync.models import jicson

_logger = logging.getLogger(__name__)

try:
    import caldav
except (ImportError, IOError) as err:
    _logger.debug(err)


class Nextcloudcaldav(models.AbstractModel):
    _name = 'nextcloud.caldav'
    
    def sync_cron(self):
        """
        Function to update events from NextCloud to Odoo and Odoo to NextCloud.
        Also logs the error and changes
        """
        # TODO:
        # -- Log all sync operation changes
        # -- Handle errors
        
        start_time = ttime.perf_counter()
        config_obj = self.env['ir.config_parameter']
        calendar_event_obj = self.env['calendar.event']
        caldav_api_credentials = {
            'url': config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_url') + '/remote.php/dav',
            'username': config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_login'),
            'pw': config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_password'),
            'enabled': config_obj.sudo().get_param('nextcloud_odoo_sync.enable_calendar_sync')
        }
        sync_log_id = self.env['nc.sync.log'].create({
            'name': datetime.now().strftime('%Y%m%d-%H%M%S'),
            'date_start': datetime.now(),
            'state': 'connecting',
            'next_cloud_url': caldav_api_credentials['url'],
            'odoo_url': self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        })
        self.env.cr.commit()
        
        caldav_api_credentials_copy = caldav_api_credentials.copy()
        caldav_api_credentials_copy.update({'pw': '*****'})
        connection = False
        if caldav_api_credentials['enabled']:
            connection = self.check_nextcloud_connection(url=caldav_api_credentials['url'], username=caldav_api_credentials['username'], password=caldav_api_credentials['pw'])
            sync_log_id.line_ids.create({
                'log_id': sync_log_id.id,
                'operation_type': 'login',
                'data_send': caldav_api_credentials_copy,
                'response_description': connection.get('response_description') if isinstance(connection, dict) else str(connection),
                'error_code_id': connection.get('sync_error_id').id if isinstance(connection, dict) else False,
                'severity': connection.get('sync_error_id').severity if isinstance(connection, dict) else 'info'
            })
            self.env.cr.commit()
            
            if not isinstance(connection, dict):
                stg_users_nc_in_odoo = self.check_and_log_users(sync_log_id)
                for user in stg_users_nc_in_odoo:
                    if user['user_name'] == 'admin': # Remove after clarifications about nc user password
                        nc_events = self.get_all_nc_user_events(connection)
                        nc_events_hash = [hashlib.sha1(x.wire_data).hexdigest() for x in nc_events]
                        odoo_events_hash = ast.literal_eval(user['user_events_hash']) if user['user_events_hash'] else []
                        odoo_events_count = calendar_event_obj.search_count([])
                        stg_events_not_in_odoo = {'create': [], 'write': [], 'delete': []}
                        stg_events_not_in_nc = {'create': [], 'write': [], 'delete': []}
                        
                        # NextCloud -> Odoo
                        # User calendar hash not saved
                        if not odoo_events_hash:
                            stg_events_not_in_odoo['create'].extend(self.get_caldav_record(nc_events))
                        # User calendar unequal hash
                        elif odoo_events_hash != nc_events_hash:
                            # Same user calendar hash count (it means there's a modified record)
                            if len(odoo_events_hash) == len(nc_events_hash):
                                listed_nc_event = self.get_to_update_data_in_odoo(odoo_events_hash, nc_events_hash, nc_events)
                                if listed_nc_event:
                                    stg_events_not_in_odoo['write'].extend(listed_nc_event)
                            # odoo events < nextcloud events
                            elif len(odoo_events_hash) < len(nc_events_hash):
                                to_create = self.get_to_create_data_in_odoo(user, nc_events)
                                to_update = self.get_to_update_data_in_odoo(odoo_events_hash, nc_events_hash, nc_events)
                                if to_create:
                                    stg_events_not_in_odoo['create'].extend(to_create)
                                if to_update:
                                    stg_events_not_in_odoo['write'].extend(to_update)
                            # odoo events > nextcloud events
                            elif len(odoo_events_hash) > len(nc_events_hash):
                                odoo_event_ids = calendar_event_obj.search([('user_id', '=', user['user_id'][0])])
                                nc_listed_events = self.get_caldav_record(nc_events)
                                to_create = self.get_to_create_data_in_odoo(user, nc_events)
                                to_update = self.get_to_update_data_in_odoo(odoo_events_hash, nc_events_hash, nc_events)
                                for event in odoo_event_ids:
                                    if event.nc_uid not in [e['nc_uid'] for e in nc_listed_events]:
                                        vals = [{'id': event.id, 'active': False}]
                                        stg_events_not_in_odoo['delete'].extend(vals)
                                if to_create:
                                    stg_events_not_in_odoo['create'].extend(to_create)
                                if to_update:
                                    stg_events_not_in_odoo['write'].extend(to_update)
                        
                        # Odoo -> NextCloud
                        # get unsynced event records
                        unsynced_odoo_event_ids = calendar_event_obj.search([('user_id', '=', user['user_id'][0]), ('nc_synced', '=', False)])
                        # events with uid
                        calendar_event_ids = self.set_caldav_record(unsynced_odoo_event_ids.filtered(lambda x: x.nc_uid == False))
                        for odoo_events in calendar_event_ids:
                            event = {info: odoo_events[info] for info in odoo_events if info in ['summary', 'dtstart', 'dtend', 'id']} # get basic fields (temp)
                            event['odoo_id'] = event.pop('id')
                            if event:
                                stg_events_not_in_nc['create'].append(event)
                        # events without uid
                        calendar_event_ids = self.set_caldav_record(unsynced_odoo_event_ids.filtered(lambda x: x.nc_uid != False))
                        for odoo_events in calendar_event_ids:
                            event = {info: odoo_events[info] for info in odoo_events if info in ['summary', 'dtstart', 'dtend', 'uid', 'last-modified', 'id']} # get basic fields (temp)
                            event['odoo_id'] = event.pop('id')
                            if event:
                                stg_events_not_in_nc['write'].append(event)
                        # TODO: Handle delete Nextcloud event (WIP)
                        # if odoo_events_count < len(nc_events_hash):
                            # odoo_events_ids = calendar_event_obj.search([])
                            # deleted_odoo_events_uid = [x.vobject_instance.vevent.uid.value for x in nc_events if x.vobject_instance.vevent.uid.value not in odoo_events_ids.mapped('nc_uid')]
                            # stg_events_not_in_nc['delete'].extend(deleted_odoo_events_uid)
                        
                        update_user_events_hash = False
                        # Saving process: Odoo -> NextCloud
                        if stg_events_not_in_nc['create'] or stg_events_not_in_nc['write'] or stg_events_not_in_nc['delete']:
                            update_user_events_hash = True
                            calendar_obj = connection.calendar(name="Personal") # TEMP: This will change depending on the calendar event
                            if stg_events_not_in_nc['create']:
                                for item in stg_events_not_in_nc['create']:
                                    odoo_id = item.pop('odoo_id')
                                    event = calendar_obj.save_event(**item)
                                    event = calendar_obj.event(event.vobject_instance.vevent.uid.value)
                                    self.update_odoo_event(event, odoo_id)
                            if stg_events_not_in_nc['write']:
                                for not_in_nc_item in stg_events_not_in_nc['write']:
                                    # check if the save record is in stg_events_not_in_odoo. get the most recent one
                                    for not_in_odoo_item in stg_events_not_in_odoo['write']:
                                        if not_in_nc_item['uid'] == not_in_odoo_item['nc_uid']:
                                            not_in_nc_last_modif = str(not_in_nc_item.pop('last-modified'))
                                            not_in_odoo_last_modif = str(not_in_odoo_item.pop('write_date'))
                                            if not_in_nc_last_modif < not_in_odoo_last_modif:
                                                stg_events_not_in_nc['write'].remove(not_in_nc_item)
                                            elif not_in_nc_last_modif > not_in_odoo_last_modif:
                                                stg_events_not_in_odoo['write'].remove(not_in_odoo_item)
                                    if not_in_nc_item:
                                        nc_uid = not_in_nc_item.pop('uid')
                                        odoo_id = not_in_nc_item.pop('odoo_id')
                                        event = calendar_obj.event(nc_uid)
                                        if 'last-modified' in not_in_nc_item:
                                            not_in_nc_item.pop('last-modified')
                                        # TODO manage timezone upon saving
                                        [exec(f'event.vobject_instance.vevent.{i}.value = value', {'event': event, 'value': not_in_nc_item[i]}) for i in not_in_nc_item]
                                        event.save()
                                        self.update_odoo_event(event, odoo_id)
                            if stg_events_not_in_nc['delete']:
                                for i in stg_events_not_in_nc['delete']:
                                    calendar_obj.event(i).delete()
                        
                        # Saving process: NextCloud -> Odoo
                        # TODO manage timezone upon saving
                        if stg_events_not_in_odoo['create'] or stg_events_not_in_odoo['write'] or stg_events_not_in_odoo['delete']:
                            update_user_events_hash = True
                            if stg_events_not_in_odoo['create']:
                                for items in stg_events_not_in_odoo['create']:
                                    calendar_event_obj.create(items)
                            if stg_events_not_in_odoo['write']:
                                for items in stg_events_not_in_odoo['write']:
                                    self.log_event_changes(sync_log_id, 'write', items)
                                    calendar_event_obj.browse(items.pop('id')).with_context(sync=True).write(items)
                            if stg_events_not_in_odoo['delete']:
                                for items in stg_events_not_in_odoo['delete']:
                                    calendar_event_obj.browse(items.pop('id')).with_context(sync=True).write(items) 
                        
                        # Save nextcloud infos to odoo event
                        if update_user_events_hash:
                            nc_events = self.get_all_nc_user_events(connection)
                            nc_events_hash = []
                            for nc_event in nc_events:
                                calendat_event_ids = calendar_event_obj.search([('nc_uid', '=', nc_event.vobject_instance.vevent.uid.value)])
                                if calendat_event_ids:
                                    event_hash = hashlib.sha1(nc_event.wire_data).hexdigest()
                                    calendat_event_ids.with_context(sync=True).write({'nc_calendar_hash': event_hash})
                                    nc_events_hash.append(event_hash)
                            self.env['nc.sync.user'].browse(user['id']).user_events_hash = nc_events_hash if nc_events_hash else False
        else:
            sync_log_id.line_ids.create({
                'log_id': sync_log_id.id,
                'operation_type': 'login',
                'data_send': caldav_api_credentials_copy,
                'response_description': 'Calendar Sync not enabled',
                'error_code_id': False,
                'severity': 'info'
            })
            self.env.cr.commit()
        
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
        
    def update_odoo_event(self, event, odoo_id):
        """
        Function to update NextCloud related fields in odoo
        @event = Object, NextCloud event object
        @odoo_id = Int, Odoo event ID
        """
        self.env['calendar.event'].browse(odoo_id).with_context(sync=True).write({'nc_uid': event.vobject_instance.vevent.uid.value, 'nc_synced': True})
        
    def log_event_changes(self, sync_log_id, operation_type, data):
        """
        Function to log event changes
        @sync_log_id = Object, nc.sync.log object
        @operation_type = Str, Log operation type
        @data = Dict, Event data
        """
        prev_data = False
        if operation_type == 'write':
            prev_data = self.env['calendar.event'].search_read([('id', '=', data.get('id'))])
            prev_data = {item: prev_data[0].get(item) for item in data}
        sync_log_id.line_ids.create({
            'log_id': sync_log_id.id,
            'operation_type': operation_type,
            'severity': 'info',
            'prev_value': prev_data,
            'new_value': data
        })
        self.env.cr.commit()
        
    def get_to_create_data_in_odoo(self, user, nc_events):
        """
        Function to get whats to create in odoo
        @user = Dictionary, User data
        @nc_events = List, List of NextCloud events
        @return = List, List of Event data
        """
        odoo_event_ids = self.env['calendar.event'].search([('user_id', '=', user['user_id'][0])])
        nc_listed_events = self.get_caldav_record(nc_events)
        return [event for event in nc_listed_events if event['nc_uid'] not in odoo_event_ids.mapped('nc_uid')]
        
    def get_to_update_data_in_odoo(self, odoo_events_hash, nc_events_hash, nc_events):
        """
        Function to get whats to update in odoo
        @odoo_events_hash = List, Odoo event hash
        @nc_events_hash = List, NextCloud event hash
        @nc_events = List, List of NextCloud events
        @return = List, List of Event data
        """
        event_ids = self.env['calendar.event'].search([('nc_calendar_hash', 'in', [event for event in odoo_events_hash if event not in nc_events_hash]), ('nc_synced', '=', True)])
        stg_events_not_in_odoo = []
        for odoo_event in event_ids:
            for nc_event in nc_events:
                listed_nc_event = self.get_caldav_record([nc_event])
                nc_uid = listed_nc_event[0]['nc_uid'] if listed_nc_event else False
                if odoo_event.nc_uid == nc_uid:
                    listed_nc_event[0]['id'] = odoo_event.id
                    stg_events_not_in_odoo.extend(listed_nc_event)
        return stg_events_not_in_odoo
        
    def check_and_log_users(self, sync_log_id):
        """
        Function to Check and log NextCloud users information.
        @sync_log_id = Object, nc.sync.log object
        @return = List, NextCloud users that are in linked in odoo
        """
        nc_users = self.env['nextcloud.base'].get_users()["ocs"]["data"]["users"]
        odoo_users = self.env['nc.sync.user'].search_read([])
        stg_users_odoo_not_in_nc = [x for x in odoo_users if x['nextcloud_user_id'] not in [nc['id'] for nc in nc_users]]
        stg_users_nc_not_in_odoo = [x for x in nc_users if x['id'] not in [o['nextcloud_user_id'] for o in odoo_users]]
        stg_users_nc_in_odoo = [x for x in odoo_users if x['nextcloud_user_id'] in [nc['id'] for nc in nc_users]]
        if stg_users_odoo_not_in_nc:
            sync_log_id.line_ids.create({
                'log_id': sync_log_id.id,
                'operation_type': 'read',
                'severity': 'info',
                'response_description': f"Odoo Users not in NextCloud: {', '.join([x['nextcloud_user_id'] for x in stg_users_odoo_not_in_nc])}"
            })
        if stg_users_nc_not_in_odoo:
            sync_log_id.line_ids.create({
                'log_id': sync_log_id.id,
                'operation_type': 'read',
                'severity': 'info',
                'response_description': f"NextCloud users not in Odoo: {', '.join([x['id'] for x in stg_users_nc_not_in_odoo])}"
            })
        self.env.cr.commit()
        return stg_users_nc_in_odoo

    def check_nextcloud_connection(self, url, username, password):
        """
        Function to test NextCloud connection.
        @user = string, NextCloud URL
        @username, string = NextCloud username
        @password = string, NextCloud password
        @return = Bool
        """
        with caldav.DAVClient(url=url, username=username, password=password) as client:
            try:
                return client.principal()
            except caldav.lib.error.AuthorizationError as e:
                return {
                    'sync_error_id': self.env.ref('nextcloud_odoo_sync.nc_sync_error_1000'),
                    'response_description': str(e)
                }
            except (caldav.lib.error.PropfindError, requests.exceptions.ConnectionError) as e:
                return {
                    'sync_error_id': self.env.ref('nextcloud_odoo_sync.nc_sync_error_1001'),
                    'response_description': str(e)
                }
                
    def get_all_nc_user_events(self, client=False):
        if not client:
            client = caldav.DAVClient('https://nctest.volendra.net/remote.php/dav', username='admin', password='D3f@ult101').principal()
        calendar = client.calendar()
        return calendar.events()

    def get_caldav_event(self, user, date_from, date_to, calendar):
        """
        Function to get user calendar in NextCloud.
        @user = integer, user ID of NextCloud
        @date_from, date_to = date object, determine the range, default to current date
        @calendar = string, name of user calendar to query, default to all calendars if no value
        @return = list of events
        """
        with caldav.DAVClient('https://nctest.volendra.net/remote.php/dav', username='admin', password='D3f@ult101') as client:
            my_principal = client.principal()
            calendar_obj = my_principal.calendar(name=calendar)
            events_fetched = calendar_obj.search(start=date_from, end=date_to, event=True, expand=True)
            return self.get_events_listed_dict(events_fetched)

    def get_caldav_record(self, event):
        """
        Function for parsing CalDav event and return a dictionary of Odoo field and values ready for create/write operation.
        @event = string, event data
        @return = dictionary of Odoo fields with data
        """
        result = []
        for rec in event:
            vevent = jicson.fromText(rec.data).get('VCALENDAR')[0].get('VEVENT')[0]
            odoo_field_mapping = self.get_caldav_fields()
            vals = {}
            for e in vevent:
                field_name = e.lower().split(";")[0]
                if field_name in odoo_field_mapping:
                    try:
                        data = parse(vevent[e]).strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        data = vevent[e]
                    vals[odoo_field_mapping[field_name]] = data
            vals.update({'nc_calendar_hash': hashlib.sha1(rec.wire_data).hexdigest(),
                         'nc_synced': True}) # TODO: Populate the additional NextCloud fields
            result.append(vals)
        return result

    def get_caldav_fields(self):
        """
        Function for mapping of CalDav fields to Odoo fields
        @return = dictionary of CalDav fields as key with Odoo ir.model.fields single recordset as value
        """
        return {
            'summary': 'name',
            'dtstart': 'start',
            'dtend': 'stop',
            'description': 'description',
            'organizer': 'user_id',
            'location': 'location',
            'attendee': 'partner_ids',
            'categories': 'categ_ids',
            'repeat': 'recurrency',
            'freebusy': 'show_as',
            'uid': 'nc_uid',
            'last-modified': 'write_date',
            'id': 'id'
        }

    def set_caldav_record(self, event):
        """
        Function for creating event in CalDav format for sending into NextCloud.
        @event - Odoo calendar single or multiple recordset
        @return - List of multiple CalDav format events
        """
        result = []
        odoo_field_mapping = {v: k for k, v in self.get_caldav_fields().items()}
        event_ids = event.search_read([('id', 'in', event.ids)])
        for e in event_ids:
            vals = {}
            for field in odoo_field_mapping:
                if field in odoo_field_mapping and field in e and e[field]:
                    if field in ['start', 'stop']:
                        vals[odoo_field_mapping[field]] = self.get_local_datetime(e[field])
                    elif field in ['user_id', 'partner_ids']:  # TODO: Handle these fields
                        continue
                    else:
                        vals[odoo_field_mapping[field]] = e[field]
            result.append(vals)
        return result

    def get_events_listed_dict(self, events_obj):
        result = []
        for event in events_obj:
            vcalendar = jicson.fromText(event.data)
            result.extend(vcalendar.get('VCALENDAR'))
        return result

    def get_local_datetime(self, datetime_datetime):
        user_tz = self.env.user.tz or pytz.utc
        local = pytz.timezone(user_tz)
        return datetime_datetime.astimezone(local)
    
    def convert_readable_time_duration(self, total_time):
        td_str = str(timedelta(seconds=total_time))
        x = td_str.split(':')
        return f"{x[1]} minutes {round(float(x[2]))} seconds" if x[1] != '00' else f"{round(float(x[2]))} Seconds"
