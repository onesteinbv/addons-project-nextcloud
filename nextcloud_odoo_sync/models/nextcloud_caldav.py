# -*- coding: utf-8 -*-
# Copyright (c) 2022 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import logging
import requests
import pytz
from dateutil.parser import parse
from datetime import datetime
from odoo import api, models, fields, tools, SUPERUSER_ID, _
from odoo.exceptions import UserError
from odoo.http import request
from odoo.addons.nextcloud_odoo_sync.models import jicson

_logger = logging.getLogger(__name__)

try:
    import caldav
except (ImportError, IOError) as err:
    _logger.debug(err)


class Nextcloudcaldav(models.AbstractModel):
    _name = 'nextcloud.caldav'

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
        # with caldav.DAVClient('https://nctest.volendra.net/remote.php/dav', username='admin', password='D3f@ult101') as client:
        #     my_principal = client.principal()
        #     calendar_obj = my_principal.calendar(name='Personal')
        #     event = calendar_obj.search(start=datetime(2022, 12, 12), end=datetime(2022, 12, 12, 23, 59, 59), summary='Test', event=True, expand=True)
        vevent = jicson.fromText(event[0].data).get('VCALENDAR')[0].get('VEVENT')[0]
        odoo_field_mapping = self.get_caldav_fields()
        result = {}
        for e in vevent:
            if e.lower() in odoo_field_mapping:
                try:
                    data = parse(vevent[e]).strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    data = vevent[e]
                result[odoo_field_mapping[e.lower()]] = data
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
            'freebusy': 'show_as'
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
