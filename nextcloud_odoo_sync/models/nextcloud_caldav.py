# -*- coding: utf-8 -*-
# Copyright (c) 2022 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import logging
import requests
import pytz
import time as ttime
import hashlib
import ast
import json
from bs4 import BeautifulSoup
from dateutil.parser import parse
from datetime import datetime, timedelta
from odoo import models
from odoo.addons.nextcloud_odoo_sync.models import jicson

_logger = logging.getLogger(__name__)

try:
    import caldav
    from icalendar import Calendar, Event, Alarm
except (ImportError, IOError) as err:
    _logger.debug(err)


class Nextcloudcaldav(models.AbstractModel):
    _name = 'nextcloud.caldav'

    def get_caldav_credentials(self):
        config_obj = self.env['ir.config_parameter']
        res = {'url': config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_url') + '/remote.php/dav',
               'username': config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_login'),
               'pw': config_obj.sudo().get_param('nextcloud_odoo_sync.nextcloud_password'),
               'enabled': config_obj.sudo().get_param('nextcloud_odoo_sync.enable_calendar_sync')}
        return res

    def sync_cron(self):
        """
        Function to update events from NextCloud to Odoo and Odoo to NextCloud.
        Also logs the error and changes
        """
        self = self.sudo()
        start_time = ttime.perf_counter()
        log_obj = self.env['nc.sync.log']

        calendar_event_obj = self.env['calendar.event']
        caldav_api_credentials = self.get_caldav_credentials()

        # Start Sync Process: Date + Time
        sync_start = datetime.now()
        result = log_obj.log_event('pre_sync')
        sync_log_id = result['log_id']
        if sync_log_id and result['resume']:
            stg_users_nc_in_odoo = result['stg_users_nc_in_odoo']
            create_count = write_count = delete_count = error_count = 0
            for user in stg_users_nc_in_odoo:
                connection, connection_principal = self.check_nextcloud_connection(url=caldav_api_credentials['url'], username=user['user_name'], password=user['nc_password'])
                if isinstance(connection_principal, dict) and connection_principal.get('sync_error_id'):
                    error_id = connection_principal.get('sync_error_id')
                    error = '%s %s' % (error_id.name, error_id.description)
                    log_obj.log_event('error', sync_log_id, error=error, message='Nextcloud:')
                    _logger.warning('Error: %s' % error)
                    continue

                # Collect events in both Odoo and NextCloud
                log_obj.log_event('text', sync_log_id, message='Getting events for "%s"' % user['user_name'])
                _logger.warning('Getting events for "%s"' % user['user_name'])
                try:
                    nc_events = self.get_all_nc_user_events(connection_principal)
                    nc_events_hash = self.get_event_hash('list', nc_events)
                except Exception as error:
                    log_obj.log_event('error', sync_log_id, error=error, message='Nextcloud:')
                    _logger.warning('Error: %s' % error)
                try:
                    odoo_events_hash = ast.literal_eval(user['user_events_hash']) if user['user_events_hash'] else []
                except Exception as error:
                    log_obj.log_event('error', sync_log_id, error=error, message='Odoo:')
                    _logger.warning('Error: %s' % error)
                stg_events_not_in_odoo = {'create': [], 'write': [], 'delete': []}
                stg_events_not_in_nc = {'create': [], 'write': [], 'delete': []}

                # NextCloud -> Odoo
                log_obj.log_event('text', sync_log_id, message='Comparing events for "%s"' % user['user_name'])
                _logger.warning('Comparing events for %s' % user['user_name'])
                try:
                    # User calendar hash not saved
                    if not odoo_events_hash:
                        stg_events_not_in_odoo['create'].extend(self.get_caldav_record(nc_events, user))
                    # User calendar unequal hash
                    elif odoo_events_hash != nc_events_hash:
                        # Same user calendar hash count (it means there's a modified record)
                        if len(odoo_events_hash) == len(nc_events_hash):
                            listed_nc_event = self.get_to_update_data_in_odoo(odoo_events_hash, nc_events_hash, nc_events, user)
                            if listed_nc_event:
                                stg_events_not_in_odoo['write'].extend(listed_nc_event)

                        # odoo events < nextcloud events
                        elif len(odoo_events_hash) < len(nc_events_hash):
                            to_create = self.get_to_create_data_in_odoo(user, nc_events)
                            to_update = self.get_to_update_data_in_odoo(odoo_events_hash, nc_events_hash, nc_events, user)
                            if to_create:
                                stg_events_not_in_odoo['create'].extend(to_create)
                            if to_update:
                                stg_events_not_in_odoo['write'].extend(to_update)

                        # odoo events > nextcloud events
                        elif len(odoo_events_hash) > len(nc_events_hash):
                            odoo_event_ids = calendar_event_obj.search([('user_id', '=', user['user_id'][0])])
                            nc_uids = [x.vobject_instance.vevent.uid.value for x in nc_events]
                            calendar_event_ids = self.env['calendar.event'].search([('nc_uid', 'in', nc_uids)])
                            nc_listed_events = self.get_caldav_record(nc_events, user, calendar_event_ids)
                            to_create = self.get_to_create_data_in_odoo(user, nc_events)
                            to_update = self.get_to_update_data_in_odoo(odoo_events_hash, nc_events_hash, nc_events, user)
                            for event in odoo_event_ids:
                                if event.nc_uid not in [e['nc_uid'] for e in nc_listed_events]:
                                    stg_events_not_in_odoo['delete'].append({'id': event.id})
                            if to_create:
                                stg_events_not_in_odoo['create'].extend(to_create)
                            if to_update:
                                stg_events_not_in_odoo['write'].extend(to_update)
                    else:
                        to_update = self.get_events_with_updated_calendar(nc_events, user)
                        if to_update:
                            stg_events_not_in_odoo['write'].extend(to_update)

                    # Odoo -> NextCloud
                    # get unsynced event records
                    all_odoo_event_ids = calendar_event_obj.search([('user_id', '=', user['user_id'][0])])
                    unsynced_odoo_event_ids = all_odoo_event_ids.filtered(lambda x: not x.nc_synced)
                    # events with uid
                    calendar_event_ids = self.set_caldav_record(unsynced_odoo_event_ids.filtered(lambda x: x.nc_uid == False and x.nc_to_delete == False))
                    for odoo_events in calendar_event_ids:
                        odoo_events['odoo_id'] = odoo_events.pop('id')
                        if odoo_events:
                            stg_events_not_in_nc['create'].append(odoo_events)
                    # events without uid
                    calendar_event_ids = self.set_caldav_record(unsynced_odoo_event_ids.filtered(lambda x: x.nc_uid != False and x.nc_to_delete == False))
                    for odoo_events in calendar_event_ids:
                        odoo_events['odoo_id'] = odoo_events.pop('id')
                        all_day = odoo_events.pop('all_day', False)
                        if odoo_events:
                            if not all_day and self.env['calendar.event'].browse(odoo_events['odoo_id']).nc_allday:
                                stg_events_not_in_nc['delete'].append(odoo_events.copy())
                                stg_events_not_in_nc['create'].append(odoo_events.copy())
                                stg_events_not_in_odoo['write'].append({'id': odoo_events['odoo_id'], 'nc_allday': all_day})
                            else:
                                stg_events_not_in_nc['write'].append(odoo_events.copy())
                    # To delete handling
                    to_delete_calendar_event_ids = unsynced_odoo_event_ids.filtered(lambda x: x.nc_to_delete == True)
                    if to_delete_calendar_event_ids:
                        to_delete_nc = [{'uid': event} for event in to_delete_calendar_event_ids.mapped('nc_uid') if event]
                        if to_delete_nc:
                            stg_events_not_in_nc['delete'].extend(to_delete_nc)
                        stg_events_not_in_odoo['delete'].extend([{'id': odoo_event.id} for odoo_event in to_delete_calendar_event_ids])
                except Exception as error:
                    log_obj.log_event('error', sync_log_id, error=error)
                    _logger.warning('Error: %s' % error)

                update_user_events_hash = False
                # Log operation count
                all_stg_events = {'Nextcloud': stg_events_not_in_nc, 'Odoo': stg_events_not_in_odoo}
                for stg_events in all_stg_events:
                    message = '%s:' % stg_events
                    for operation in all_stg_events[stg_events]:
                        count = len(all_stg_events[stg_events][operation])
                        message += ' %s events to %s,' % (count, operation)
                    log_obj.log_event('text', sync_log_id, message=message.strip(','))

                # Saving process: Odoo -> NextCloud
                if stg_events_not_in_nc['create'] or stg_events_not_in_nc['write'] or stg_events_not_in_nc['delete']:
                    nc_start = datetime.now()
                    log_obj.log_event('text', sync_log_id, message='Updating Nextcloud events')
                    _logger.warning('Updating Nextcloud events')
                    update_user_events_hash = True
                    if stg_events_not_in_nc['create']:
                        for item in stg_events_not_in_nc['create']:
                            try:
                                odoo_id = item.pop('odoo_id')
                                nc_calendar = item.pop('nc_calendar_ids')
                                attendees = item.pop('attendee')
                                nc_valarm = item.pop('valarm', [])
                                item.pop('uid', False)
                                item.pop('last-modified', False)
                                calendar_obj = self.get_user_calendar(connection, connection_principal, nc_calendar)
                                event = calendar_obj.save_event(**item)
                                event = calendar_obj.event(event.vobject_instance.vevent.uid.value)
                                event = self.add_nc_alarm_data(event, nc_valarm)
                                if attendees:
                                    event.parent.save_with_invites(event.icalendar_instance, attendees=attendees, schedule_agent="NONE")
                                event.save()
                                self.update_odoo_event(event, odoo_id)
                                create_count += 1
                            except Exception as error:
                                log_obj.log_event('error', sync_log_id, error=error, message='Error creating Nextcloud event for %s:\n' % user['user_name'])
                                _logger.warning('Error creating Nextcloud event for %s: %s' % (user['user_name'], error))
                                error_count += 1
                                continue
                    if stg_events_not_in_nc['write']:
                        for not_in_nc_item in stg_events_not_in_nc['write']:
                            try:
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
                                    nc_calendar = not_in_nc_item.pop('nc_calendar_ids')
                                    nc_valarm = not_in_nc_item.pop('valarm', [])
                                    attendees = not_in_nc_item.pop('attendee')
                                    old_calendar_obj = self.get_old_calendar_object(connection_principal, nc_uid)
                                    event = False
                                    # Event not moved in to another calendar
                                    if old_calendar_obj:
                                        if old_calendar_obj.name.lower() == nc_calendar.replace(' ', '-').lower():
                                            event = old_calendar_obj.event(nc_uid)
                                        # Event moved in to another calendar
                                        else:
                                            old_event = old_calendar_obj.event_by_uid(nc_uid)
                                            event_data = old_event.data
                                            new_calendar_obj = self.get_user_calendar(connection, connection_principal, nc_calendar)
                                            event = new_calendar_obj.add_event(event_data)
                                            old_event.delete()
                                    if event:
                                        if 'last-modified' in not_in_nc_item:
                                            not_in_nc_item.pop('last-modified')
                                        # update alarms
                                        event = self.add_nc_alarm_data(event, nc_valarm)
                                        # update transp (busy/free)
                                        if not event.vobject_instance.vevent.contents.get('transp', False):
                                            event.icalendar_component.add('transp', not_in_nc_item.pop('transp'))

                                        if 'description' not in not_in_nc_item:
                                            event.vobject_instance.vevent.contents.pop('description', False)
                                        elif not event.vobject_instance.vevent.contents.get('description', False):
                                            event.icalendar_component.add('description', not_in_nc_item.pop('description'))

                                        if 'location' not in not_in_nc_item:
                                            event.vobject_instance.vevent.contents.pop('location', False)
                                        elif not event.vobject_instance.vevent.contents.get('location', False):
                                            event.icalendar_component.add('location', not_in_nc_item.pop('location'))

                                        # update every field
                                        [exec(f'event.vobject_instance.vevent.{i}.value = val', {'event': event, 'val': not_in_nc_item[i]}) for i in not_in_nc_item]

                                        # Reset attendees
                                        if 'attendee' in event.vobject_instance.vevent.contents:
                                            event.vobject_instance.vevent.contents.pop('attendee')
                                        # Save new attendees
                                        # attendees.append(connection_principal.get_vcal_address())
                                        if attendees:
                                            event.parent.save_with_invites(event.icalendar_instance, attendees=attendees, schedule_agent="NONE")

                                        event.save()
                                        self.update_odoo_event(event, odoo_id)
                                write_count += 1
                            except Exception as error:
                                log_obj.log_event('error', sync_log_id, error=error, message='Error updating Nextcloud event for %s:\n' % user['user_name'])
                                _logger.warning('Error updating Nextcloud event for %s: %s' % (user['user_name'], error))
                                error_count += 1
                                continue

                    if stg_events_not_in_nc['delete']:
                        log_obj.log_event('text', sync_log_id, message='Nextcloud: Deleting records', operation_type='delete')
                        for i in stg_events_not_in_nc['delete']:
                            calendar_obj = self.get_old_calendar_object(connection_principal, i['uid'])
                            try:
                                calendar_obj.event(i['uid']).delete()
                                delete_count += 1
                            except Exception as error:
                                log_obj.log_event('error', sync_log_id, error=error, message='Error deleting Nextcloud event for %s:\n' % user['user_name'])
                                _logger.warning('Error deleting Nextcloud event for %s: %s' % (user['user_name'], error))
                                error_count += 1
                                continue
                    hours, minutes, seconds = log_obj.get_time_diff(nc_start)
                    log_obj.log_event('text', sync_log_id, message='Update Nextcloud duration: %s:%s:%s' % (hours, minutes, seconds))

                # Saving process: NextCloud -> Odoo
                if stg_events_not_in_odoo['create'] or stg_events_not_in_odoo['write'] or stg_events_not_in_odoo['delete']:
                    od_start = datetime.now()
                    log_obj.log_event('text', sync_log_id, message='Updating Odoo events')
                    update_user_events_hash = True
                    if stg_events_not_in_odoo['create']:
                        log_obj.log_event('text', sync_log_id, message='Odoo: Creating records', operation_type='create')
                        for items in stg_events_not_in_odoo['create']:
                            try:
                                # TODO: Handle issue with duplicate event in Odoo when the user is an attendee
                                calendar_event_obj.with_context(sync=True).sudo().create(items)
                                create_count += 1
                            except Exception as error:
                                log_obj.log_event('error', sync_log_id, error=error, message='Error creating Odoo event for %s:\n' % user['user_name'])
                                _logger.warning('Error creating Odoo event for %s: %s' % (user['user_name'], error))
                                error_count += 1
                                continue
                    if stg_events_not_in_odoo['write']:
                        for items in stg_events_not_in_odoo['write']:
                            try:
                                calendar_event_obj.browse(items.pop('id')).with_context(sync=True).sudo().write(items)
                                write_count += 1
                            except Exception as error:
                                log_obj.log_event('error', sync_log_id, error=error, message='Error updating Odoo event for %s:\n' % user['user_name'])
                                _logger.warning('Error updating Odoo event for %s: %s' % (user['user_name'], error))
                                continue
                    if stg_events_not_in_odoo['delete']:
                        log_obj.log_event('text', sync_log_id, message='Odoo: Deleting records', operation_type='delete')
                        for items in stg_events_not_in_odoo['delete']:
                            try:
                                calendar_event_obj.browse(items.pop('id')).with_context(sync=True).sudo().unlink()
                            except Exception as error:
                                log_obj.log_event('error', sync_log_id, error=error, message='Error deleting Odoo event for %s:\n' % user['user_name'])
                                _logger.warning('Error deleting Odoo event for %s: %s' % (user['user_name'], error))
                                delete_count += 1
                                continue

                    hours, minutes, seconds = log_obj.get_time_diff(od_start)
                    log_obj.log_event('text', sync_log_id, message='Update Odoo duration: %s:%s:%s' % (hours, minutes, seconds))

                # Save nextcloud infos to odoo event
                if update_user_events_hash:
                    nc_events_to_save = self.get_all_nc_user_events(connection_principal)
                    nc_events_hash_to_save = []
                    for nc_event_to_save in nc_events_to_save:
                        calendat_event_ids = calendar_event_obj.search([('nc_uid', '=', nc_event_to_save.vobject_instance.vevent.uid.value)])
                        if calendat_event_ids:
                            event_hash = self.get_event_hash('str', nc_event_to_save)
                            calendat_event_ids.with_context(sync=True).write({'nc_calendar_hash': event_hash})
                            nc_events_hash_to_save.append(event_hash)
                    self.env['nc.sync.user'].browse(user['id']).user_events_hash = nc_events_hash_to_save if nc_events_hash_to_save else False

        errors = sync_log_id.line_ids.filtered(lambda x: x.severity in ['error', 'critical'])
        warnings = sync_log_id.line_ids.filtered(lambda x: x.severity in ['warning'])
        infos = sync_log_id.line_ids.filtered(lambda x: not x.severity or x.severity in ['info'])
        sync_log_id.description = f'{len(errors)} Error(s), {len(warnings)} Warning(s) and {len(infos)} Info(s)'

        end_time = ttime.perf_counter()
        elapsed = end_time - start_time
        duration = round(elapsed, 2)
        sync_log_id.duration = self.convert_readable_time_duration(duration)

        hours, minutes, seconds = log_obj.get_time_diff(sync_start)
        summary_message = '''Sync process duration: %s:%s:%s\n - Total create %s\n - Total write %s\n - Total delete %s\n - Total error %s''' % (
            hours, minutes, seconds, create_count, write_count, delete_count, error_count)
        log_obj.log_event('text', sync_log_id, message=summary_message)

    def add_nc_alarm_data(self, event, valarm):
        if valarm:
            event.vobject_instance.vevent.contents.pop('valarm', False)
            for item in valarm:
                alarm_obj = Alarm()
                alarm_obj.add("action", "DISPLAY")
                alarm_obj.add("TRIGGER;RELATED=START", item)
                event.icalendar_component.add_component(alarm_obj)
        return event

    def get_events_with_updated_calendar(self, nc_events, user):
        to_update_events = []
        nc_uids = [x.vobject_instance.vevent.uid.value for x in nc_events]
        calendar_event_ids = self.env['calendar.event'].search([('nc_uid', 'in', nc_uids)])
        for i_nc in nc_events:
            calendar_event_id = calendar_event_ids.filtered(lambda x: x.nc_uid == i_nc.vobject_instance.vevent.uid.value)
            nc_calendar = calendar_event_id.nc_calendar_ids.filtered(lambda x: x.user_id == calendar_event_id.user_id)
            if nc_calendar and calendar_event_id and nc_calendar.calendar_url != i_nc.parent.canonical_url:
                result = self.get_caldav_record([i_nc], user, calendar_event_ids)
                result[0].update({'id': calendar_event_id.id})
                to_update_events.extend(result)
        return to_update_events

    def get_user_calendar(self, connection, connection_principal, nc_calendar):
        principal_calendar_obj = False
        try:
            principal_calendar_obj = connection_principal.calendar(name=nc_calendar)
            principal_calendar_obj.events()
            calendar_obj = connection.calendar(url=principal_calendar_obj.url)
        except:
            calendar_obj = connection_principal.make_calendar(name=nc_calendar)
        return calendar_obj

    def get_old_calendar_object(self, connection_principal, nc_uid, name=False):
        calendars = connection_principal.calendars()
        for record in calendars:
            try:
                record.event_by_uid(nc_uid)
            except:
                continue
            return record

    def get_event_hash(self, mode, event):
        """
        Function to get NextCloud event hash
        @mode = str, function mode
        @event = Object, NextCloud event object
        @return = List / String
        """
        result = False
        if mode == 'list':
            result = []
            for nc_event_item in event:
                vevent = jicson.fromText(nc_event_item.data).get('VCALENDAR')[0].get('VEVENT')[0]
                vevent = json.dumps(vevent, sort_keys=True)
                result.append(hashlib.sha1(vevent.encode('utf-8')).hexdigest())
        elif mode == 'str':
            vevent = jicson.fromText(event.data).get('VCALENDAR')[0].get('VEVENT')[0]
            vevent = json.dumps(vevent, sort_keys=True)
            result = hashlib.sha1(vevent.encode('utf-8')).hexdigest()
        return result

    def update_odoo_event(self, event, odoo_id):
        """
        Function to update NextCloud related fields in odoo
        @event = Object, NextCloud event object
        @odoo_id = Int, Odoo event ID
        """
        self.env['calendar.event'].browse(odoo_id).with_context(sync=True).write({'nc_uid': event.vobject_instance.vevent.uid.value, 'nc_synced': True})

    def get_to_create_data_in_odoo(self, user, nc_events):
        """
        Function to get whats to create in odoo
        @user = Dictionary, User data
        @nc_events = List, List of NextCloud events
        @return = List, List of Event data
        """
        odoo_event_ids = self.env['calendar.event'].search([('user_id', '=', user['user_id'][0])])
        nc_uids = [x.vobject_instance.vevent.uid.value for x in nc_events]
        calendar_event_ids = self.env['calendar.event'].search([('nc_uid', 'in', nc_uids)])
        nc_listed_events = self.get_caldav_record(nc_events, user, calendar_event_ids)
        return [event for event in nc_listed_events if event['nc_uid'] not in odoo_event_ids.mapped('nc_uid')]

    def get_to_update_data_in_odoo(self, odoo_events_hash, nc_events_hash, nc_events, user):
        """
        Function to get whats to update in odoo
        @odoo_events_hash = List, Odoo event hash
        @nc_events_hash = List, NextCloud event hash
        @nc_events = List, List of NextCloud events
        @return = List, List of Event data
        """
        nc_uids = [x.vobject_instance.vevent.uid.value for x in nc_events]
        calendar_event_ids = self.env['calendar.event'].search([('nc_uid', 'in', nc_uids)])
        event_ids = self.env['calendar.event'].search([('nc_calendar_hash', 'in', [event for event in odoo_events_hash if event not in nc_events_hash]), ('nc_synced', '=', True)])
        stg_events_not_in_odoo = []
        for odoo_event in event_ids:
            for nc_event in nc_events:
                listed_nc_event = self.get_caldav_record([nc_event], user, calendar_event_ids)
                nc_uid = listed_nc_event[0]['nc_uid'] if listed_nc_event else False
                if odoo_event.nc_uid == nc_uid:
                    listed_nc_event[0]['id'] = odoo_event.id
                    stg_events_not_in_odoo.extend(listed_nc_event)
        return stg_events_not_in_odoo

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
                return client, client.principal()
            except caldav.lib.error.AuthorizationError as e:
                _logger.warning('Error: %s' % e)
                return client, {
                    'sync_error_id': self.env.ref('nextcloud_odoo_sync.nc_sync_error_1000'),
                    'response_description': str(e)
                }
            except (caldav.lib.error.PropfindError, requests.exceptions.ConnectionError) as e:
                _logger.warning('Error: %s' % e)
                return client, {
                    'sync_error_id': self.env.ref('nextcloud_odoo_sync.nc_sync_error_1001'),
                    'response_description': str(e)
                }

    def get_all_nc_user_events(self, client=False):
        calendars = client.calendars()
        result = []
        for calendar in calendars:
            events = calendar.events()
            if events:
                result.extend(calendar.events())
        return result

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

    def get_odoo_attendees(self, nc_attendees, user):
        result = []
        organizer_user_id = self.env['res.users'].browse(user['user_id'][0])
        result.append(organizer_user_id.partner_id.id)
        for record in nc_attendees:
            value = record.split(':')[-1]
            contact_id = self.env['res.partner'].search([('email', '=', value)], limit=1)
            if contact_id:
                result.append(contact_id.id)
            else:
                contact_id = self.env['res.partner'].create({'name': value, 'email': value, 'nc_sync': True})
                result.append(contact_id.id)
        return [(6, 0, result)]

    def get_alarms_mapping(self):
        return {
            'PT0S': self.env.ref('nextcloud_odoo_sync.alarm_notif_at_event_start').id,
            '-PT5M': self.env.ref('nextcloud_odoo_sync.alarm_notif_5_mins').id,
            '-PT10M': self.env.ref('nextcloud_odoo_sync.alarm_notif_10_mins').id,
            '-PT15M': self.env.ref('calendar.alarm_notif_1').id,
            '-PT30M': self.env.ref('calendar.alarm_notif_2').id,
            '-PT1H': self.env.ref('calendar.alarm_notif_3').id,
            '-PT2H': self.env.ref('calendar.alarm_notif_4').id,
            '-P1D': self.env.ref('calendar.alarm_notif_5').id,
            '-P2D': self.env.ref('nextcloud_odoo_sync.alarm_notif_2_days').id
        }

    def get_odoo_alarms(self, valarm):
        result = []
        alarms_mapping = self.get_alarms_mapping()
        for v_item in valarm:
            v_item.pop('ACTION', False)
            val = [v_item[x] for x in v_item]
            if val:
                result.append(alarms_mapping.get(val[0]))
        return [(6, 0, result)]

    def get_caldav_record(self, event, user, calendar_event_ids=False):
        """
        Function for parsing CalDav event and return a dictionary of Odoo field and values ready for create/write operation.
        @event = string, event data
        @return = dictionary of Odoo fields with data
        """
        result = []
        calendar_ids = self.env['nc.calendar'].search([('user_id', '=', user['user_id'][0])])
        for record in event:
            vevent = jicson.fromText(record.data).get('VCALENDAR')[0].get('VEVENT')[0]
            odoo_field_mapping = self.get_caldav_fields()
            vals = {}
            nc_attendees = [value.value for value in record.vobject_instance.vevent.contents.get('attendee', []) if value]
            all_day = False
            for e in vevent:
                field_name = e.lower().split(";")
                if field_name[0] in odoo_field_mapping:
                    data = False
                    try:
                        date_data = parse(vevent[e])
                        tz = field_name[-1].split('=')[-1]
                        if tz != 'date':
                            data = self.convert_date(date_data, tz, 'utc')
                        else:
                            if field_name[0] == 'dtstart':
                                data = date_data.date()
                            elif field_name[0] == 'dtend':
                                data = date_data.date() - timedelta(days=1)
                            all_day = True
                    except Exception:
                        data = vevent[e]
                    if field_name[0] == 'transp':
                        if vevent[e].lower() == 'opaque':
                            data = 'busy'
                        elif vevent[e].lower() == 'transparent':
                            data = 'free'
                    elif field_name[0] == 'status':
                        status_vals = {
                            'confirmed': self.env.ref('nextcloud_odoo_sync.nc_event_status_confirmed').id,
                            'tentative': self.env.ref('nextcloud_odoo_sync.nc_event_status_tentative').id,
                            'canceled': self.env.ref('nextcloud_odoo_sync.nc_event_status_canceled').id
                        }
                        data = status_vals[vevent[e].lower()]
                    elif field_name[0] == 'valarm':
                        data = self.get_odoo_alarms(vevent.get(e, []))
                    if data:
                        vals[odoo_field_mapping[field_name[0]]] = data
            calendar_id = calendar_ids.filtered(lambda x: x.calendar_url == record.parent.canonical_url)
            if not calendar_id:
                calendar_id = self.env['nc.calendar'].with_context(sync=True).create({'name': record.parent.name, 'user_id': user['user_id'][0], 'calendar_url': record.parent.canonical_url})
            if all_day:
                vals['start_date'] = vals.pop('start')
                vals['stop_date'] = vals.pop('stop')
            vals.update({'partner_ids': self.get_odoo_attendees(nc_attendees, user),
                         'allday': all_day,
                         'nc_allday': all_day,
                         'user_id': user['user_id'][0],
                         'nc_calendar_hash': self.get_event_hash('str', record),
                         'nc_synced': True})  # TODO: Populate the additional NextCloud fields
            if calendar_event_ids:
                nc_calendar_ids = calendar_event_ids.filtered(lambda x: x.nc_uid == vals['nc_uid']).mapped('nc_calendar_ids')
                new_nc_calendar_ids = list(set(nc_calendar_ids.ids) - set(calendar_ids.ids))
            else:
                new_nc_calendar_ids = []
            new_nc_calendar_ids.append(calendar_id.id)
            vals['nc_calendar_ids'] = [(6, 0, new_nc_calendar_ids)]
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
            'status': 'nc_status',
            'location': 'location',
            'attendee': 'partner_ids',
            'categories': 'categ_ids',
            'repeat': 'recurrency',
            'transp': 'show_as',
            'uid': 'nc_uid',
            'valarm': 'alarm_ids',
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
        odoo_field_mapping.update({'nc_calendar_ids': 'nc_calendar_ids'})
        event_ids = event.search_read([('id', 'in', event.ids)])
        for e in event_ids:
            vals = {}
            for field in odoo_field_mapping:
                if field in odoo_field_mapping and field in e and e[field]:
                    if field in ['start', 'stop']:
                        if 'allday' in e and e['allday']:
                            vals['all_day'] = True
                            if field == 'start':
                                vals[odoo_field_mapping[field]] = e['start_date']
                            elif field == 'stop':
                                vals[odoo_field_mapping[field]] = e['stop_date'] + timedelta(days=1)
                        else:
                            vals[odoo_field_mapping[field]] = self.get_local_datetime(e[field])
                    elif field == 'partner_ids':
                        vals[odoo_field_mapping[field]] = self.get_attendees(e[field], e)
                    elif field == 'description':
                        soup = BeautifulSoup(e[field])
                        description = soup.get_text('\n')
                        if description != '':
                            vals[odoo_field_mapping[field]] = soup.get_text('\n')
                    elif field == 'nc_calendar_ids':
                        # Get the value related to the user_id
                        event_id = self.env['calendar.event'].browse(e['id'])
                        calendar_id = event_id.nc_calendar_ids.filtered(lambda x: x.user_id == event_id.user_id)
                        if calendar_id:
                            vals[odoo_field_mapping[field]] = self.env['nc.calendar'].browse(calendar_id.ids[0]).name
                        else:
                            vals[odoo_field_mapping[field]] = ''
                    elif field == 'show_as':
                        if e[field] == 'free':
                            vals[odoo_field_mapping[field]] = 'TRANSPARENT'
                        elif e[field] == 'busy':
                            vals[odoo_field_mapping[field]] = 'OPAQUE'
                    elif field == 'nc_status':
                        vals[odoo_field_mapping[field]] = e[field][1].upper()
                    elif field == 'alarm_ids':
                        vals[odoo_field_mapping[field]] = self.get_nc_vevent_values(e[field])
                    else:
                        vals[odoo_field_mapping[field]] = e[field]
            result.append(vals)
        return result

    def get_nc_vevent_values(self, value):
        alarms_mapping = {v: k for k, v in self.get_alarms_mapping().items()}
        return [alarms_mapping.get(x) for x in value]

    def get_attendees(self, val, event):
        result = []
        user_id = self.env['res.users'].browse(event['user_id'][0])
        nc_user_ids = self.env['nc.sync.user'].search([])
        for partner_id in self.env['res.partner'].browse(val).filtered(lambda x: x.id != user_id.partner_id.id):
            nc_user_id = nc_user_ids.filtered(lambda x: x.user_id.partner_id.email == partner_id.email)
            if nc_user_id:
                url = self.env['ir.config_parameter'].sudo().get_param('nextcloud_odoo_sync.nextcloud_url') + '/remote.php/dav'
                client, principal = self.check_nextcloud_connection(url=url, username=nc_user_id.user_name, password=nc_user_id.nc_password)
                result.append(principal.get_vcal_address())
            else:
                result.append(f"mailto:{partner_id.email}")
        return result

    def convert_date(self, dt, tz, mode):
        """
        This method converts datetime object to UTC and vice versa
        @param: dt, datetime object
        @param: tz, string (e.g. 'Asia/Manila')
        @param: mode, string ('utc':local time -> utc, 'local':utc -> local time)
        @retun: datetime
        """
        dt_conv = False
        if mode and dt and tz:
            if mode == 'utc':
                dt_tz = pytz.timezone(tz).localize(dt, is_dst=None)
                dt_conv = dt_tz.astimezone(pytz.utc).replace(tzinfo=None)
            if mode == 'local':
                dt_tz = dt.replace(tzinfo=pytz.utc)
                dt_conv = dt_tz.astimezone(pytz.timezone(tz)).replace(tzinfo=None)
        return dt_conv

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
