# -*- coding: utf-8 -*-
# Copyright (c) 2022 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import logging
import requests
import pytz
import hashlib
import ast
import json
from odoo.tools import html2plaintext
from dateutil.parser import parse
from datetime import datetime, timedelta
from odoo import models
from odoo.addons.nextcloud_odoo_sync.models import jicson
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

try:
    import caldav
    from icalendar import Alarm
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

    def compare_events(self, od_events, nc_events):
        """
        This method compares the Odoo and Nextcloud events and returns the value to be created, modified or delete
        :param od_events: list of dictionary of Odoo events
        :param nc_events: list of dictionary of Nexcloud events
        Case summary
            Nextcloud
                - If event hash value changes from hash value recorded in counterpart event in Odoo, update event in Odoo
                - If event UID value does not exist in any Odoo events, add new event in Odoo
                - If existing UID in Odoo no longer exist in Nextcloud, delete event in Odoo
            Odoo
                - If nc_synced value is False, a change happen in Odoo event, update event in Nextcloud
                - If to_delete value is True, the event needs to be deleted in Nextcloud first, then delete in Odoo after sync
                - If event in Odoo has no UID and hash, create the event in Nextcloud (its a new event from Odoo)
            Conflict
                - If event hash value changes and event in Odoo has nc_synced = False, means both had updated the event prior to the sync
                    * Need to check both event most recent modified date to determine which is the most recent then override the other event
                - If event is recurring and UID is shared between multiple calendar event in Nextcloud, delete and recreate all recurring events
                    * We can only delete and recreate since there is no way we can identify the single instance of recurring event in Nextcloud 
                    because they share the same UID. Some instance of a recurring event can have a change in date and time that is out of the
                    recurrence rule scope, so we can't rely on the recurrence rule to identify these events
        """
        od_events_dict = {'create': [], 'write': [], 'delete': []}
        nc_events_dict = {'create': [], 'write': [], 'delete': []}

        # Compare Odoo events to sync
        if od_events and nc_events:
            # Odoo -> Nextcloud
            for ode in od_events:
                # Case 1: Event created in Odoo and not yet synced to Nextcloud (nc_synced=False, nc_uid=False)
                if not ode['nc_uid'] and not ode['od_event'].nc_synced:
                    nc_events_dict['create'].append(ode)
                if ode['nc_uid'] and ode['event_hash']:
                    valid_nc_uid = False
                    for nce in nc_events:
                        if ode['nc_uid'] == nce['nc_uid']:
                            valid_nc_uid = True
                            # If a matching event was found then save the caldav event in od_events_dict and save the odoo event in nc_events_dict
                            ode['nc_caldav'] = nce['nc_caldav']
                            nce['od_event'] = ode['od_event']
                            # Case 2: If both hash values are the same
                            if ode['event_hash'] == nce['event_hash']:
                                # Case 2.a: If Odoo event has no changes to sync (nc_synced=True) then no change to update to Nextcloud
                                if ode['od_event'].nc_synced:
                                    break
                                else:
                                    if ode['od_event'].nc_status.name.lower() != 'canceled':
                                        # Case 2.b: If there are changes to sync (nc_synced=False) and to delete (nc_to_delete=True), delete Nextcloud event
                                        if not ode['od_event'].nc_synced and ode['od_event'].nc_to_delete:
                                            nc_events_dict['delete'].append(ode)
                                        # Case 2.c: If there are changes to sync (nc_sycned=False) but not to delete (nc_to_delete=False), update Nextcloud event
                                        else:
                                            nc_events_dict['write'].append(ode)
                                    else:
                                        nc_events_dict['delete'].append(ode)
                            # Case 3: If both hash differs
                            else:
                                # Case 3.a: If Odoo event has no change (nc_synced=True) then update based on Nextcloud
                                if ode['od_event'].nc_synced:
                                    # delete if cancelled
                                    if nce['nc_caldav'].vobject_instance.vevent.status.value == 'CANCELLED':
                                        od_events_dict['delete'].append(nce)
                                    else:
                                        if nce not in od_events_dict['write']:
                                            od_events_dict['write'].append(nce)
                                else:
                                    # Case 3.b: If Odoo has changes (nc_synced=False) and to delete (nc_to_delete=True), delete Nextcloud event
                                    if not ode['od_event'].nc_synced and ode['od_event'].nc_to_delete:
                                        nc_events_dict['delete'].append(ode)
                                    # Case 3.c: If Odoo has changes (nc_synced=False) and not to delete (nc_to_delete=False)
                                    else:
                                        # Check LAST-MODIFIED date value in Nextcloud event
                                        if 'LAST-MODIFIED' in nce['nc_event'][0]:
                                            # The "Z" stands for Zulu time (zero hours ahead of GMT) which is another name for UTC
                                            nc_last_modified = datetime.strptime(nce['nc_event'][0]['LAST-MODIFIED'], "%Y%m%dT%H%M%SZ")
                                            od_last_modified = ode['od_event'].write_date
                                            if od_last_modified > nc_last_modified:
                                                nc_events_dict['write'].append(ode)
                                            else:
                                                od_events_dict['write'].append(nce)

                    # Case 4: If the value of Odoo nc_uid is not found in all of Nextcloud events, then it was deleted in Nextcloud
                    if not valid_nc_uid:
                        od_events_dict['delete'].append(ode)
            # Nextcloud -> Odoo
            for nce in nc_events:
                valid_nc_uid = False
                for ode in od_events:
                    if nce['nc_uid'] == ode['nc_uid']:
                        valid_nc_uid = True
                        break
                # Case 5: Nextcloud nc_uid is not found in Odoo
                if not valid_nc_uid:
                    od_events_dict['create'].append(nce)
        # Case 6: If there is not a single event in Odoo, we create everything from Nextcloud -> Odoo
        if not od_events and nc_events:
            for rec in nc_events:
                # ignore if cancelled
                if rec['nc_caldav'].vobject_instance.vevent.status.value != 'CANCELLED':
                    od_events_dict['create'].append(rec)
        # Case 7: If there is not a single event in Nextcloud, check if Odoo event has nc_uid value or not
        if od_events and not nc_events:
            for ode in od_events:
                # ignore if cancelled
                if ode['od_event'].nc_status.name.lower() != 'canceled':
                    # Case 7.a: If the event has an existing nc_uid value, then its a previous event in Nextcloud that might have been deleted
                    if ode['od_event'].nc_uid:
                        od_events_dict['delete'].append(ode)
                    else:
                        # Case 7.b: If the event has no nc_uid value then its a new event in Odoo to be created in Nextcloud
                        nc_events_dict['create'].append(ode)
        return od_events_dict, nc_events_dict

    def get_event_attendees(self, calendar_event, user_id, **params):
        """
        This method check if the attendee is a user of Odoo or a user of Nextcloud or an external contact
        :param calendar_event: caldav Event object or Odoo calendar.event recordset
        :param user_id: single recordset of res.users model
        :param **params: dictionary of keyword arguments containing multiple recordset of models
        :return list of res.partner model record ids
        """
        all_user_ids = params.get('all_user_ids', False)
        all_sync_user_ids = params.get('all_sync_user_ids', False)
        all_partner_ids = params.get('all_partner_ids', False)
        attendee_partner_ids = []
        # Get attendees for Odoo event
        if isinstance(calendar_event, caldav.objects.Event):
            # In Odoo, we add the organizer are part of the meeting attendee
            attendee_partner_ids = [user_id.partner_id.id]
            try:
                attendees = [value.value for value in calendar_event.vobject_instance.vevent.contents.get('attendee', []) if value]
            except Exception:
                attendees = []
            for att in attendees:
                email = att.split(':')[-1].lower()
                if email != 'false':
                    # Check if an Odoo user has the same email address
                    att_user_id = all_user_ids.filtered(lambda x: x.partner_id.email and x.partner_id.email.lower() == email)
                    # In case more than 1 user has the same email address, check which user is in nc.sync.user model
                    if att_user_id and len(user_id.ids) > 1:
                        sync_user_id = all_sync_user_ids.filtered(lambda x: x.user_id.id in att_user_id.ids)
                        if sync_user_id:
                            attendee_partner_ids.append(self.env['nc.sync.user'].browse(sync_user_id.ids[0]).user_id.partner_id.id)
                        else:
                            attendee_partner_ids.append(self.env['res.users'].browse(user_id.ids[0]).partner_id.id)
                    else:
                        contact_id = all_partner_ids.filtered(lambda x: x.email.lower() == email)
                        if contact_id:
                            attendee_partner_ids.append(contact_id.ids[0])
                        else:
                            contact_id = self.env['res.partner'].create({'name': email, 'email': email, 'nc_sync': True})
                            all_partner_ids |= contact_id
                            attendee_partner_ids.append(contact_id.id)
        # Get attendees for Nextcloud event
        elif isinstance(calendar_event, models.Model) and calendar_event.partner_ids and all_sync_user_ids:
            for partner in calendar_event.partner_ids:
                # In Nextcloud, we don't populate the attendee if there is only the organizer involve
                if partner != user_id.partner_id or len(calendar_event.partner_ids) > 1:
                    nc_user_id = all_sync_user_ids.filtered(lambda x: x.partner_id.id == partner.id)
                    try:
                        nc_user_connection, nc_user_principal = nc_user_id.get_user_connection()
                    except Exception as e:
                        nc_user_connection = nc_user_principal = False
                    if nc_user_id and nc_user_principal:
                        attendee_partner_ids.append(nc_user_principal.get_vcal_address())
                    else:
                        # Get only partner_ids with email address
                        if partner.email:
                            attendee_partner_ids.append(f"mailto:{partner.email}")
        return attendee_partner_ids, all_partner_ids

    def get_event_datetime(self, nc_field, nc_value, vals, od_event=False, ):
        """
        This method will parse the Nextcloud event date, convert it to datetime in UTC timezone
        :param: nc_field: Nextcloud ical data field name
        :param: nc_value: Nextcloud ical data field value
        :param: vals: Dictionary of event values
        :param: od_event: single recordset of calendar.event model
        :return: date, datetime or string
        """
        try:
            tz = nc_field[-1].split('=')[-1]
            if tz not in ('date', 'exdates'):
                if "Z" in nc_value:
                    nc_value = nc_value.replace("Z", "")
                date_value = parse(nc_value)
                if od_event and od_event.allday and not isinstance(date_value, datetime):
                    data = date_value.date()
                else:
                    data = self.convert_date(date_value, tz, 'utc')
                return data
            elif tz == 'exdates' and isinstance(nc_value, list) and 'exdate_tz' in vals:
                data = []
                all_day = False
                if od_event and od_event.allday:
                    all_day = True
                for item in nc_value:
                    if "Z" in item:
                        item = item.replace("Z", "")
                    date_value = parse(item)
                    if vals['exdate_tz']:
                        date_value = self.convert_date(parse(item), vals['exdate_tz'], 'utc')
                    if all_day and isinstance(date_value, datetime):
                        date_value = parse(item).date()
                    data.append(date_value)
                return data
            else:
                data = parse(nc_value).date()
                if nc_field[0] == 'dtend':
                    data = data - timedelta(days=1)
                return data
        except Exception as e:
            _logger.warning(e)
            return nc_value

    def manage_recurring_instance(self, event_dict, operation, vals):
        """
        This method manages the changes for a sinlge instance of a recurring event
        :param event_dict: dictionary, Odoo and Nextcloud events
        :param operation: string, indicate whether its create, write or delete operation
        :return single recordset of calendar.event model, string, dictionary of values
        """
        exdates = []
        caldav_event = event_dict.get('nc_caldav', False)
        event_id = event_dict.get('od_event', False)
        date_format = '%Y%m%dT%H%M%S'
        if event_id.allday:
            date_format = '%Y%m%d'
        if caldav_event:
            prev_exdates = caldav_event.vobject_instance.vevent.contents.pop('exdate', False)
            if prev_exdates:
                exdates = prev_exdates[0].value
        if event_id.recurrence_id.nc_exdate:
            od_exdates = [parse(x) for x in ast.literal_eval(event_id.recurrence_id.nc_exdate)]
            [exdates.append(d) for d in od_exdates if d not in exdates]
        # Handle create and delete operation
        if exdates:
            if operation == 'create':
                # Check for detached events in Odoo
                detach_ids = event_id.recurrence_id.calendar_event_ids.filtered(lambda x: x.nc_detach)
                if detach_ids:
                    detach_exdates = [parse(x.nc_rid) for x in detach_ids]
                    [exdates.append(d) for d in detach_exdates if d not in exdates]
                vals['exdate'] = exdates
            if operation == 'delete':
                # Check if all instance of recurring events are for deletion
                to_delete_ids = event_id.recurrence_id.calendar_event_ids.filtered(lambda x: x.nc_to_delete)
                if not to_delete_ids or len(to_delete_ids.ids) != len(event_id.recurrence_id.calendar_event_ids.ids):
                    operation = 'null'
        # Handle write operation by marking the existing caldav_event with exdate
        # then create a new caldav_event that is detached from recurring rule
        if operation == 'write' or event_id.nc_detach:
            [vals.pop(x, False) for x in ['rrule', 'uid', 'exdate'] if x in vals]
            exdate = parse(event_id.nc_rid)
            if exdate not in exdates:
                exdates.append(exdate)
                event_id.recurrence_id.nc_exdate = [x.strftime(date_format) for x in exdates]
            operation = 'create'
        # Set the exdates value in the caldav_event
        if exdates and caldav_event:
            caldav_event.icalendar_component.add('exdate', exdates)
            caldav_event.save()
        return event_id, operation, vals

    def get_rrule_dict(self, rrule):
        rrule = rrule.split(':')[-1]
        result = {}
        for rec in rrule.split(';'):
            k, v = rec.split('=')
            try:
                v = int(v)
            except:
                pass
            result.update({k: v})
        return result

    def update_event_hash(self, hash_vals, event_ids=False):
        """
        This method updates the hash value of the event        
        :param hash_vals: dict, dictionary of sync user and event hash values
        :param event_ids: single/multiple recordset of calendar.event model
        """
        # Update the hash value of the Odoo event that corresponds to the current user_id
        if event_ids:
            event_ids.nc_synced = True
            for event in event_ids:
                hash_id = event.nc_hash_ids.filtered(lambda x: x.nc_sync_user_id.id == hash_vals['nc_sync_user_id'])
                if hash_id:
                    hash_id.nc_event_hash = hash_vals['nc_event_hash']
                else:
                    # Create the hash value for the event if not exist
                    hash_vals['calendar_event_id'] = event.id
                    self.env['calendar.event.nchash'].create(hash_vals)

        elif not event_ids and 'principal' in hash_vals:
            events_hash = hash_vals['events']
            principal = hash_vals['principal']
            sync_user_id = hash_vals['sync_user_id']
            calendars = principal.calendars()
            all_user_events = []
            for calendar in calendars:
                events = calendar.events()
                if events:
                    all_user_events.extend(calendar.events())
            for event in all_user_events:
                nc_uid = event.vobject_instance.vevent.uid.value
                if nc_uid in events_hash:
                    event_vals = sync_user_id.get_event_data(event)
                    new_vals = {'nc_sync_user_id': sync_user_id.id, 'nc_event_hash': event_vals['hash']}
                    event_id = events_hash[nc_uid]
                    if event_id.recurrence_id:
                        event_id = event_id.recurrence_id.calendar_event_ids
                        event_id.nc_uid = nc_uid
                    self.update_event_hash(new_vals, event_id)

    def update_attendee_invite(self, event_ids):
        """
        This method accepts the invitation for the meeting organizer as part of attendee        
        :param event_ids: single/multiple recordset of calendar.event model
        """
        for event in event_ids:
            attendee_id = event.attendee_ids.filtered(lambda x: x.partner_id == event.user_id.partner_id)
            if attendee_id:
                attendee_id.state = 'accepted'

    def delete_exempted_event(self, event_id, exdates, all_odoo_event_ids):
        """
        This method deletes an instance of a recurring event that was already deleted in Nextcloud
        :param event_id: single recordset of calendar.event model
        :param exdates: dictionary of event UID and EXDATE value in string from Nextcloud
        :param all_odoo_event_ids: multiple recordset of alendar.event model
        :return recordset: returns a new list of all_odoo_event_ids where delete events were removed
        """
        if exdates and event_id.nc_uid in exdates and event_id.recurrence_id:
            ex_recurring_event_ids = event_id.recurrence_id.calendar_event_ids.filtered(lambda x: x.nc_rid in exdates[event_id.nc_uid])
            if ex_recurring_event_ids:
                all_odoo_event_ids = all_odoo_event_ids.filtered(lambda x: x not in ex_recurring_event_ids)
                ex_recurring_event_ids.sudo().with_context(force_delete=True).unlink()
        return all_odoo_event_ids

    def update_odoo_events(self, sync_user_id, od_events_dict, **params):
        """
        This method updates the Odoo calendar.event records from Caldav events
        :param sync_user_id: single recordset of nc.sync.user model
        :param od_events_dict: dictionary of create, write and delete operations for Odoo
        :param **params: dictionary of keyword arguments containing multiple recordset of models
        """
        all_odoo_event_ids = params.get('all_odoo_event_ids', False)
        all_nc_calendar_ids = params.get('all_nc_calendar_ids', False)
        status_vals = params.get('status_vals', False)
        calendar_event = self.env['calendar.event'].sudo()
        field_mapping = self.get_caldav_fields()
        log_obj = params['log_obj']
        user_id = sync_user_id.user_id
        user_name = sync_user_id.user_id.name
        for operation in od_events_dict:
            for event in od_events_dict[operation]:
                od_event_id = event['od_event'] if 'od_event' in event else False
                nc_event = event['nc_event'] if 'nc_event' in event else False
                caldav_event = event['nc_caldav'] if 'nc_caldav' in event else False
                event_hash = event['event_hash']
                exdates = {}
                if nc_event:
                    for vevent in nc_event:
                        vals = {'nc_uid': event['nc_uid']}
                        nc_uid = event['nc_uid']
                        new_event_id = False
                        all_day = False
                        # Loop through each fields from the Nextcloud event and map it to Odoo fields
                        for e in vevent:
                            field = e.lower().split(";")
                            if field[0] in field_mapping or field[0] == 'exdates':
                                data = self.get_event_datetime(field, vevent[e], vevent, od_event_id)
                                if field[0] == 'dtstart' and not isinstance(data, datetime):
                                    all_day = True
                                if field[0] == 'transp':
                                    if vevent[e].lower() == 'opaque':
                                        data = 'busy'
                                    elif vevent[e].lower() == 'transparent':
                                        data = 'free'
                                elif field[0] == 'status' and status_vals:
                                    data = status_vals[vevent[e].lower()]
                                elif field[0] == 'valarm':
                                    data = self.get_odoo_alarms(vevent.get(e, []))
                                elif field[0] == 'categories':
                                    data = self.get_odoo_categories(self.env['calendar.event.type'].search([]), vevent[e])
                                elif field[0] == 'rrule':
                                    vals['recurrency'] = True
                                    vals['rrule'] = data
                                    data = False
                                elif field[0] == 'exdates' and data:
                                    if nc_uid not in exdates:
                                        exdates[nc_uid] = []
                                    for item in data:
                                        if isinstance(item, datetime):
                                            item = item.strftime('%Y%m%dT%H%M%S')
                                        else:
                                            item = item.strftime('%Y%m%d')
                                        exdates[nc_uid].append(item)
                                    data = False
                                elif field[0] == 'recurrence-id' and data:
                                    if isinstance(data, datetime):
                                        data = data.strftime('%Y%m%dT%H%M%S')
                                    else:
                                        data = data.strftime('%Y%m%d')
                                if data:
                                    vals[field_mapping[field[0]]] = data

                        if all_day:
                            vals['start_date'] = vals.pop('start')
                            vals['stop_date'] = vals.pop('stop')
                        # Populate the nc_calendar_ids field in Odoo
                        nc_calendar_id = all_nc_calendar_ids.filtered(lambda x: x.calendar_url == caldav_event.parent.canonical_url and x.user_id == user_id)
                        if all_odoo_event_ids:
                            event_nc_calendar_ids = all_odoo_event_ids.filtered(lambda x: x.nc_uid == vals['nc_uid']).mapped('nc_calendar_ids')
                            user_nc_calendar_ids = all_nc_calendar_ids.filtered(lambda x: x.user_id == user_id)
                            new_nc_calendar_ids = list(set(event_nc_calendar_ids.ids) - set(user_nc_calendar_ids.ids))
                        else:
                            new_nc_calendar_ids = []
                        if nc_calendar_id:
                            new_nc_calendar_ids.append(nc_calendar_id.id)
                        vals['nc_calendar_ids'] = [(6, 0, new_nc_calendar_ids)]
                        # clear categ_ids when not present
                        if 'categ_ids' not in vals:
                            vals['categ_ids'] = [(6, 0, [])]
                        # clear alarm_ids when not present
                        if 'alarm_ids' not in vals:
                            vals['alarm_ids'] = [(6, 0, [])]
                        # Set the status
                        if 'nc_status' not in vals:
                            vals['nc_status'] = self.env.ref('nextcloud_odoo_sync.nc_event_status_confirmed').id
                        # Populate attendees and rest of remaining fields
                        event_name = vals['name']
                        vals.pop('write_date', False)
                        attendee_partner_ids, params['all_partner_ids'] = self.get_event_attendees(caldav_event, user_id, **params)
                        hash_vals = {'nc_sync_user_id': sync_user_id.id, 'nc_event_hash': event_hash}
                        vals.update({'partner_ids': [(6, 0, attendee_partner_ids)],
                                     'allday': all_day,
                                     'nc_allday': all_day,
                                     'user_id': user_id.id,
                                     'nc_synced': True})

                        # Perform create operation
                        if operation == 'create':
                            try:
                                new_event_id = False
                                # Check if the event is part of recurring event
                                if 'nc_rid' in vals and nc_uid:
                                    recurring_event_id = all_odoo_event_ids.filtered(lambda x: x.nc_uid == nc_uid and x.nc_rid == vals['nc_rid'])
                                    if recurring_event_id:
                                        recurring_event_id.write(vals)
                                        self.update_attendee_invite(recurring_event_id)
                                        self.update_event_hash(hash_vals, recurring_event_id)
                                        all_odoo_event_ids = self.delete_exempted_event(recurring_event_id, exdates, all_odoo_event_ids)
                                else:
                                    vals['nc_hash_ids'] = [(0, 0, hash_vals)]
                                    new_event_id = calendar_event.create(vals)
                                    if new_event_id.recurrence_id and new_event_id.recurrence_id.calendar_event_ids:
                                        recurring_event_ids = new_event_id.recurrence_id.calendar_event_ids
                                        all_odoo_event_ids |= recurring_event_ids
                                        self.update_attendee_invite(recurring_event_ids)
                                        self.update_event_hash(hash_vals, recurring_event_ids)
                                        all_odoo_event_ids = self.delete_exempted_event(new_event_id, exdates, all_odoo_event_ids)

                                    else:
                                        all_odoo_event_ids |= new_event_id
                                # In Odoo, the organizer is by default part of the attendee and automatically accepts the invite
                                # Accepted calendar event in Odoo appears with background filled in Calendar view
                                if new_event_id:
                                    self.update_attendee_invite(new_event_id)
                                # Commit the changes to the database
                                self.env.cr.commit()
                            except Exception as e:
                                message = 'Error creating Odoo event "%s" for user "%s":\n' % (event_name, user_name)
                                log_obj.log_event(mode='error', error=e, message='Error creating Odoo event', operation_type='create')

                        # Perform write operation
                        if operation == 'write' and od_event_id:
                            try:
                                # Process exempted dates: these are deleted recurring instance in Nextcloud
                                all_odoo_event_ids = self.delete_exempted_event(od_event_id, exdates, all_odoo_event_ids)
                                # We don't update if the event only contains rrule but no nc_rid
                                if 'rrule' in vals and 'nc_rid' not in vals:
                                    continue
                                # Check if the event is part of recurring event
                                elif 'rrule' not in vals and 'nc_rid' in vals:
                                    recurring_event_id = od_event_id.recurrence_id.calendar_event_ids.filtered(lambda x: x.nc_rid == vals['nc_rid'])
                                    if not recurring_event_id:
                                        continue
                                    else:
                                        od_event_id = recurring_event_id
                                od_event_id.write(vals)
                                # Update the hash value of the Odoo event that corresponds to the current user_id
                                if od_event_id.recurrence_id:
                                    od_event_id = od_event_id.recurrence_id.calendar_event_ids
                                # Update hash values and attendee invite
                                self.update_attendee_invite(od_event_id)
                                self.update_event_hash(hash_vals, od_event_id)
                                # Commit the changes to the database
                                self.env.cr.commit()
                            except Exception as e:
                                message = 'Error updating Odoo event "%s" for user "%s":\n' % (event_name, user_name)
                                log_obj.log_event(mode='error', error=e, message=message, operation_type='write')

                # Perform delete operation
                if operation == 'delete' and 'od_event' in event and event['od_event']:
                    event['od_event'].sudo().with_context(force_delete=True).unlink()
        return params['all_partner_ids']

    def update_nextcloud_events(self, sync_user_id, nc_events_dict, **params):
        """
        This method updates the Nexcloud calendar event records from Odoo
        :param sync_user_id: single recordset of nc.sync.user model
        :param nc_events_dict: dictionary of create, write and delete operations for Nextcloud
        :param **params: dictionary of keyword arguments containing multiple recordset of models
        """
        connection = params.get('connection', False)
        principal = params.get('principal', False)
        fields = self.env['calendar.event']._fields
        update_events_hash = {}
        # Reverse the mapping of the field in get_caldav_fiels() method for Odoo -> Nextcloud direction
        field_mapping = {v: k for k, v in self.get_caldav_fields().items()}
        alarms_mapping = {v: k for k, v in self.get_alarms_mapping().items()}
        log_obj = params['log_obj']
        user_name = sync_user_id.user_id.name
        recurrent_rule_ids = {}
        for operation in nc_events_dict:
            recurrent_rule_ids[operation] = []
            prev_operation = operation
            for event in nc_events_dict[operation]:
                event_id = event['od_event']
                caldav_event = event.get('nc_caldav', False)
                if event_id.recurrence_id:
                    if event_id.recurrence_id in recurrent_rule_ids[operation] and not event_id.nc_detach:
                        continue
                    else:
                        if operation == 'create' and not event_id.nc_detach:
                            # Get the first event of the recurring event by sorting it by record id
                            event_ids = event_id.recurrence_id.calendar_event_ids.sorted(key=lambda r: r.id)
                            event_id = self.env['calendar.event'].browse(event_ids.ids[0])
                attendees = []
                vals = {}
                # Loop through each fields from the Nextcloud event and map it to Odoo fields with values
                for field in field_mapping:
                    if field not in fields or not event_id[field] or field in ['id', 'write_date', 'nc_rid']:
                        continue
                    value = event_id[field]
                    if field in ['start', 'stop']:
                        if 'allday' in event_id and event_id['allday']:
                            start_stop = {'start': event_id['start_date'],
                                          'stop': event_id['stop_date'] + timedelta(days=1)}
                            vals[field_mapping[field]] = start_stop[field]
                        else:
                            user_tz = sync_user_id.user_id.tz
                            vals[field_mapping[field]] = self.convert_date(value, user_tz, 'local')
                    elif field == 'partner_ids':
                        attendees, params['all_partner_ids'] = self.get_event_attendees(event_id, sync_user_id.user_id, **params)
                    elif field == 'description':
                        description = html2plaintext(value)
                        if description != '' or description:
                            vals[field_mapping[field]] = description
                    elif field == 'show_as':
                        show_as = {'free': 'TRANSPARENT', 'busy': 'OPAQUE'}
                        vals[field_mapping[field]] = show_as[value]
                    elif field == 'nc_status':
                        vals[field_mapping[field]] = value.name.upper()
                    elif field == 'alarm_ids':
                        vals[field_mapping[field]] = [alarms_mapping.get(x.id) for x in value]
                    elif field == 'categ_ids':
                        vals[field_mapping[field]] = value.mapped('name')
                    elif field == 'recurrence_id':
                        if value not in recurrent_rule_ids[operation]:
                            recurrent_rule_ids[operation].append(value)
                            rrule = self.get_rrule_dict(value._rrule_serialize())
                            vals[field_mapping[field]] = rrule
                    else:
                        vals[field_mapping[field]] = value
                # Get the Nextcloud calendar
                event_name = vals['summary']
                nc_calendar_id = sync_user_id.nc_calendar_id if sync_user_id.nc_calendar_id else False
                if event_id['nc_calendar_ids']:
                    event_nc_calendar_id = event_id.nc_calendar_ids.filtered(lambda x: x.user_id == event_id.user_id)
                    if event_nc_calendar_id:
                        nc_calendar_id = event_nc_calendar_id

                if event_id.recurrence_id:
                    event_id, operation, vals = self.manage_recurring_instance(event, operation, vals)
                elif not event_id.recurrence_id and operation != prev_operation:
                    operation = prev_operation

                # Perform create operation
                if operation == 'create':
                    # Check if event is recurrent and there are exempted dates
                    try:
                        if nc_calendar_id and connection and principal:
                            calendar_obj = self.get_user_calendar(connection, principal, nc_calendar_id.name)
                            new_caldav_event = calendar_obj.save_event(**vals)
                            caldav_event = new_caldav_event
                            # After creating the event, add attendees and alarms data
                            event = self.add_nc_alarm_data(caldav_event, vals.get('valarm', []))
                            if attendees:
                                new_caldav_event.parent.save_with_invites(caldav_event.icalendar_instance, attendees=attendees, schedule_agent="NONE")
                        else:
                            raise ValidationError(_('No Nextcloud calendar specified for the event %s for user %s' % (event_id.name, event_id.user_id.name)))
                    except Exception as e:
                        message = 'Error creating Nextcloud event "%s" for user "%s":\n' % (event_name, user_name)
                        log_obj.log_event(mode='error', error=e, message=message, operation_type='create')

                # Perform write operation
                if operation == 'write' and caldav_event:
                    try:
                        if 'recurrence-id' in vals and 'rrule' in vals:
                            vals.pop('rrule')
                        # Check if there is a change in nc_calendar in Odoo
                        od_calendar_url = nc_calendar_id.calendar_url if nc_calendar_id else False
                        nc_calendar_url = caldav_event.parent.canonical_url
                        if od_calendar_url != nc_calendar_url and nc_calendar_id:
                            # If the nc_calendar was changed in Odoo, delete the existing event in Nextcloud old calendar
                            # and recreate the same event in Nextcloud on the new calendar
                            event_data = caldav_event.data
                            new_calendar_obj = self.get_user_calendar(connection, principal, nc_calendar_id.name)
                            caldav_event = new_calendar_obj.add_event(event_data)
                            old_event = caldav_event.event_by_uid(event_id.nc_uid)
                            old_event.delete()
                        # Remove the current event dtstart and dtend values if its an allday event in Nextcloud but not in Odoo
                        if not event_id.allday and event_id.nc_allday:
                            caldav_event.vobject_instance.vevent.contents.pop('dtstart')
                            caldav_event.vobject_instance.vevent.contents.pop('dtend')
                        # Update the event alarms (alarm_ids)
                        event = self.add_nc_alarm_data(caldav_event, vals.pop('valarm', []))
                        # Handle special case when no value exist for some fields
                        for f in ['transp', 'description', 'location', 'dtstart', 'dtend']:
                            # if no value exist in Odoo, remove the field in Nextcloud event
                            if f not in vals and f != 'transp':
                                caldav_event.vobject_instance.vevent.contents.pop(f, False)
                            # if no value exist in Nextcloud, use the value from Odoo
                            elif not caldav_event.vobject_instance.vevent.contents.get(f, False):
                                caldav_event.icalendar_component.add(f, vals.pop(f))
                        # Update rest of remaining fields
                        [exec(f'caldav_event.vobject_instance.vevent.{i}.value = val', {'caldav_event': caldav_event, 'val': vals[i]}) for i in vals]
                        # Update attendees
                        if 'attendee' in caldav_event.vobject_instance.vevent.contents:
                            caldav_event.vobject_instance.vevent.contents.pop('attendee')
                        if attendees:
                            caldav_event.parent.save_with_invites(caldav_event.icalendar_instance, attendees=attendees, schedule_agent="NONE")
                    except Exception as e:
                        message = 'Error updating Nextcloud event "%s" for user "%s":\n' % (event_name, user_name)
                        log_obj.log_event(mode='error', error=e, message=message, operation_type='write')

                # Save changes to the event after create/write operation
                if operation in ('create', 'write'):
                    try:
                        caldav_event.save()
                        # Update the Odoo event record
                        res = {'nc_uid': caldav_event.vobject_instance.vevent.uid.value, 'nc_synced': True}
                        if event_id.nc_detach:
                            res.update({'recurrence_id': False, 'recurrency': False, 'nc_detach': False})
                        event_id.write(res)
                        update_events_hash[event_id.nc_uid] = event_id
                        # Commit the changes to the database since it is already been updated in Nextcloud
                        self.env.cr.commit()
                    except Exception as e:
                        message = 'Error saving event "%s" for user "%s":\n' % (event_name, user_name)
                        log_obj.log_event(mode='error', error=e, message=message, operation_type='write')

                # Perform delete operation
                if operation == 'delete':
                    try:
                        # Delete the event in Nextcloud first before deleting it in Odoo
                        caldav_event.delete()
                        # Delete all Odoo events with the same nc_uid
                        # TODO: Handle deletion of specific instance of a recurring event where nc_uid are the same
                        event_ids = params['all_odoo_event_ids'].filtered(lambda x: x.nc_uid == event_id.nc_uid)
                        event_ids.sudo().with_context(force_delete=True).unlink()
                        # Commit the changes to the database since it is already been deleted in Nextcloud
                        self.env.cr.commit()
                    except Exception as e:
                        message = 'Error deleting event "%s" for user "%s":\n' % (event_name, user_name)
                        log_obj.log_event(mode='error', error=e, message=message, operation_type='delete')

        # Update hash value of events
        if update_events_hash:
            try:
                hash_vals = {'principal': principal, 'events': update_events_hash, 'sync_user_id': sync_user_id}
                self.update_event_hash(hash_vals)
            except Exception as e:
                message = 'Error updating hash values of events for user "%s":\n' % user_name
                log_obj.log_event(mode='error', error=e, message=message)

    def sync_cron(self):
        """
        This method triggers the sync event operation
        """
        self = self.sudo()
        # Start Sync Process: Date + Time
        sync_start = datetime.now()
        result = self.env['nc.sync.log'].log_event('pre_sync')
        log_obj = result['log_id']
        # To minimize impact on performance, search only once rather than searching each loop
        params = {
            'log_obj': result['log_id'],
            'all_odoo_event_ids': self.env['calendar.event'].search([]),
            'all_nc_calendar_ids': self.env['nc.calendar'].search([('user_id', '!=', False)]),
            'all_user_ids': self.env['res.users'].search([]),
            'all_sync_user_ids': self.env['nc.sync.user'].search([]),
            'all_partner_ids': self.env['res.partner'].search([('email', '!=', False)]),
            'status_vals': {'confirmed': self.env.ref('nextcloud_odoo_sync.nc_event_status_confirmed').id,
                            'tentative': self.env.ref('nextcloud_odoo_sync.nc_event_status_tentative').id,
                            'cancelled': self.env.ref('nextcloud_odoo_sync.nc_event_status_canceled').id}
        }

        if result['log_id'] and result['resume']:
            sync_users = self.env['nc.sync.user'].search([('sync_calendar', '=', True)])
            create_count = write_count = delete_count = error_count = 0
            for user in sync_users:
                # Get all events from Odoo and Nextcloud
                log_obj.log_event(message='Getting events for "%s"' % user.user_id.name)
                events_dict = user.get_all_user_events(**params)
                od_events = events_dict['od_events']
                nc_events = events_dict['nc_events']
                connection = events_dict['connection']
                principal = events_dict['principal']
                if connection and principal:
                    params.update({'connection': connection, 'principal': principal})
                    # Compare all events
                    log_obj.log_event(message='Comparing events for "%s"' % user['user_name'])
                    od_events_dict, nc_events_dict = self.compare_events(od_events, nc_events)
                    # Log number of operations to do
                    all_stg_events = {'Nextcloud': nc_events_dict, 'Odoo': od_events_dict}
                    for stg_events in all_stg_events:
                        message = '%s:' % stg_events
                        for operation in all_stg_events[stg_events]:
                            count = len(all_stg_events[stg_events][operation])
                            message += ' %s events to %s,' % (count, operation)
                        log_obj.log_event(message=message.strip(','))
                    # Update events in Odoo and Nextcloud
                    log_obj.log_event(message='Updating Odoo events')
                    params['all_partner_ids'] = self.update_odoo_events(user, od_events_dict, **params)
                    log_obj.log_event(message='Updating Nextcloud events')
                    self.update_nextcloud_events(user, nc_events_dict, **params)
            # Compute duration of sync operation
            hours, minutes, seconds = log_obj.get_time_diff(sync_start)
            summary_message = '''Sync process duration: %s:%s:%s\n - Total create %s\n - Total write %s\n - Total delete %s\n - Total error %s''' % (
                hours, minutes, seconds, create_count, write_count, delete_count, error_count)
            log_obj.log_event(message=summary_message)

    def add_nc_alarm_data(self, event, valarm):
        if valarm:
            event.vobject_instance.vevent.contents.pop('valarm', False)
            for item in valarm:
                alarm_obj = Alarm()
                alarm_obj.add("action", "DISPLAY")
                alarm_obj.add("TRIGGER;RELATED=START", item)
                event.icalendar_component.add_component(alarm_obj)
        return event

    def get_user_calendar(self, connection, connection_principal, nc_calendar):
        principal_calendar_obj = False
        try:
            principal_calendar_obj = connection_principal.calendar(name=nc_calendar)
            principal_calendar_obj.events()
            calendar_obj = connection.calendar(url=principal_calendar_obj.url)
        except:
            calendar_obj = connection_principal.make_calendar(name=nc_calendar)
        return calendar_obj

    def get_event_hash(self, mode, event):
        """
        Function to get NextCloud event hash
        :param mode: str, function mode
        :param event: Object, NextCloud event object
        :return List / String
        """
        result = False
        if mode == 'list':
            result = []
            for nc_event_item in event:
                vevent = jicson.fromText(nc_event_item.data).get('VCALENDAR')[0].get('VEVENT')
                if len(vevent) > 1:
                    vals = []
                    for event in vevent:
                        event.pop('DTSTAMP', False)
                        vals.append(json.dumps(event, sort_keys=True))
                    vevent = vals
                else:
                    vevent = vevent[0]
                    vevent.pop('DTSTAMP', False)
                    vevent = json.dumps(vevent, sort_keys=True)
                result.append(hashlib.sha1(str(vevent).encode('utf-8')).hexdigest())
        elif mode == 'str':
            vevent = jicson.fromText(event.data).get('VCALENDAR')[0].get('VEVENT')
            if len(vevent) > 1:
                vals = []
                for event in vevent:
                    event.pop('DTSTAMP', False)
                    vals.append(json.dumps(event, sort_keys=True))
                vevent = vals
            else:
                vevent = vevent[0]
                vevent.pop('DTSTAMP', False)
                vevent = json.dumps(vevent, sort_keys=True)
            result = hashlib.sha1(str(vevent).encode('utf-8')).hexdigest()
        return result

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
            if isinstance(v_item, dict):
                v_item.pop('ACTION', False)
                val = [v_item[x] for x in v_item]
                if val:
                    result.append(alarms_mapping.get(val[0]))
        return [(6, 0, result)]

    def get_odoo_categories(self, categ_ids, value):
        result = []
        for category in value.lower().split(','):
            category_id = categ_ids.filtered(lambda x: x.name.lower() == category)
            if not category_id:
                category_id = categ_ids.create({'name': category})
            result.append(category_id.id)
        return [(6, 0, result)]

    def get_caldav_fields(self):
        """
        Function for mapping of CalDav fields to Odoo fields
        @return = dictionary of CalDav fields as key and Odoo calendar.event model fields as value
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
            'transp': 'show_as',
            'uid': 'nc_uid',
            'valarm': 'alarm_ids',
            'rrule': 'recurrence_id',
            'recurrence-id': 'nc_rid',
            'last-modified': 'write_date',
            'id': 'id'
        }

    def convert_date(self, dt, tz, mode):
        """
        This method converts datetime object to UTC and vice versa
        :param dt, datetime object
        :param tz, string (e.g. 'Asia/Manila')
        :param mode, string ('utc':local time -> utc, 'local':utc -> local time)
        :return: datetime
        """
        dt_conv = False
        if mode and dt and tz:
            if mode == 'utc':
                dt_tz = pytz.timezone(tz).localize(dt, is_dst=None)
                dt_conv = dt_tz.astimezone(pytz.utc).replace(tzinfo=None)
            if mode == 'local':
                dt_tz = dt.replace(tzinfo=pytz.utc)
                dt_conv = dt_tz.astimezone(pytz.timezone(tz))
        return dt_conv

    """
    Deleted functions used in first version of the sync_cron method
    -sync_cron2
    -save_recurring_events
    -get_ical_data
    -update_recurring_events
    -update_odoo_event_hash_field
    -get_excepted_recurring_events
    -get_events_with_updated_calendar
    -get_old_calendar_object
    -update_odoo_event
    -get_to_create_data_in_odoo
    -get_to_update_data_in_odoo
    -get_all_nc_user_events
    -get_caldav_event
    -get_odoo_attendees
    -get_nc_event
    -get_caldav_record
    -get_event_odoo_values
    -set_caldav_record
    -get_nc_vevent_values
    -get_attendees
    -get_events_listed_dict
    -get_local_datetime
    -convert_readable_time_duration
    """
