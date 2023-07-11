# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields, _
from dateutil import rrule
from odoo.exceptions import UserError
from datetime import datetime, time

SELECT_FREQ_TO_RRULE = {
    'daily': rrule.DAILY,
    'weekly': rrule.WEEKLY,
    'monthly': rrule.MONTHLY,
    'yearly': rrule.YEARLY,
}

RRULE_WEEKDAYS = {'SUN': 'SU', 'MON': 'MO', 'TUE': 'TU', 'WED': 'WE', 'THU': 'TH', 'FRI': 'FR', 'SAT': 'SA'}


def freq_to_rrule(freq):
    return SELECT_FREQ_TO_RRULE[freq]


class CalendarRecurrence(models.Model):
    _inherit = "calendar.recurrence"

    nc_exdate = fields.Char("Nextcloud Exdate")

    def _get_rrule(self, dtstart=None):
        self.ensure_one()
        if not self.base_event_id.nc_calendar_id or self.end_type != 'forever':
            return super()._get_rrule(dtstart)
        freq = self.rrule_type
        rrule_params = dict(
            dtstart=dtstart,
            interval=self.interval,
        )
        if freq == 'monthly' and self.month_by == 'date':  # e.g. every 15th of the month
            rrule_params['bymonthday'] = self.day
        elif freq == 'monthly' and self.month_by == 'day':  # e.g. every 2nd Monday in the month
            rrule_params['byweekday'] = getattr(rrule, RRULE_WEEKDAYS[self.weekday])(
                int(self.byday))  # e.g. MO(+2) for the second Monday of the month
        elif freq == 'weekly':
            weekdays = self._get_week_days()
            if not weekdays:
                raise UserError(_("You have to choose at least one day in the week"))
            rrule_params['byweekday'] = weekdays
            rrule_params['wkst'] = self._get_lang_week_start()
            rrule_params['count'] = ((104 // self.interval) if self.interval < 104 else 1) * len(
                weekdays)  # maximum recurring events for 2 years
        elif freq == 'daily':
            rrule_params['count'] = (
                    720 // self.interval) if self.interval < 720 else 1  # maximum recurring events for 2 years
        elif freq == 'yearly':
            rrule_params['count'] = (
                    10 // self.interval) if self.interval < 10 else 1  # maximum recurring events for 10 years
        if freq == 'monthly':
            if self.interval >= 12:
                rrule_params['count'] = (10 // (
                            self.interval // 12)) if self.interval <= 24 else 1  # maximum recurring events for 10 years
            else:
                rrule_params['count'] = (
                        24 // self.interval) if self.interval < 24 else 1  # maximum recurring events for 2 years

        return rrule.rrule(
            freq_to_rrule(freq), **rrule_params
        )
