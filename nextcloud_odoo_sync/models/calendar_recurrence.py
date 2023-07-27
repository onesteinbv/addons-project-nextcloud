# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields, _
from dateutil import rrule
from odoo.exceptions import UserError
from datetime import timedelta

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
        freq = self.rrule_type
        if self._context.get('sync_from_nextcloud',False):
            if self.until and freq in ('yearly','monthly','weekly'):
                self.until = self.until - timedelta(days=1)
        if not self.base_event_id.nc_calendar_id or self.end_type != 'forever':
            return super()._get_rrule(dtstart)

        rrule_params = dict(
            dtstart=dtstart,
            interval=self.interval,
        )
        config = self.env["ir.config_parameter"].sudo()
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
            weekly_recurring_events_limit_value = (2
                                                   if not config.get_param(
                "nextcloud_odoo_sync.weekly_recurring_events_limit")
                                                   else config.get_param(
                "nextcloud_odoo_sync.weekly_recurring_events_limit")
                                                   ) * 52
            rrule_params['count'] = ((
                                                 weekly_recurring_events_limit_value // self.interval) if self.interval < weekly_recurring_events_limit_value else 1) * len(
                weekdays)  # maximum recurring events for 2 years
        elif freq == 'daily':
            daily_recurring_events_limit_value = (2
                                                  if not config.get_param(
                "nextcloud_odoo_sync.daily_recurring_events_limit")
                                                  else config.get_param(
                "nextcloud_odoo_sync.daily_recurring_events_limit")
                                                  ) * 365
            rrule_params['count'] = (
                    daily_recurring_events_limit_value // self.interval) if self.interval < daily_recurring_events_limit_value else 1  # maximum recurring events for 2 years
        if freq in ('yearly','monthly'):
            yearly_recurring_events_limit_value = (10
                                                   if not config.get_param(
                "nextcloud_odoo_sync.yearly_recurring_events_limit")
                                                   else config.get_param(
                "nextcloud_odoo_sync.yearly_recurring_events_limit")
                                                   )
            monthly_recurring_events_limit_value = (2
                                                    if not config.get_param(
                "nextcloud_odoo_sync.monthly_recurring_events_limit")
                                                    else config.get_param(
                "nextcloud_odoo_sync.monthly_recurring_events_limit")
                                                    ) * 12
            if freq == 'yearly':
                rrule_params['count'] = (
                        yearly_recurring_events_limit_value // self.interval) if self.interval < yearly_recurring_events_limit_value else 1  # maximum recurring events for 10 years
            elif freq == 'monthly':
                if self.interval >= 12:
                    rrule_params['count'] = (yearly_recurring_events_limit_value // (
                            self.interval // 12))  # maximum recurring events for years defined
                else:
                    rrule_params['count'] = (
                            monthly_recurring_events_limit_value // self.interval)  # maximum recurring events for months defined

        return rrule.rrule(
            freq_to_rrule(freq), **rrule_params
        )
