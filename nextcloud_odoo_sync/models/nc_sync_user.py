# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)


import hashlib
import json

from datetime import datetime
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.addons.nextcloud_odoo_sync.models import jicson


class NcSyncUser(models.Model):
    _name = "nc.sync.user"
    _inherit = ["nextcloud.caldav"]
    _description = "Nextcloud Sync User"

    name = fields.Char("Odoo Username", related="user_id.name")
    nextcloud_user_id = fields.Char("Nextcloud User ID")
    user_name = fields.Char("Username", required=True)
    nc_password = fields.Char("Password", required=True)
    user_message = fields.Char(
        default="'Default Calendar' field will be used"
        "as your default Nextcloud calendar when "
        "creating new events in Odoo"
    )

    user_id = fields.Many2one(
        "res.users", "Odoo User", required=True, default=lambda self: self.env.user.id
    )
    partner_id = fields.Many2one(
        "res.partner", "Partner", related="user_id.partner_id", store=True
    )
    nc_calendar_id = fields.Many2one("nc.calendar", "Default Nextcloud Calendar")
    nc_hash_ids = fields.One2many(
        "calendar.event.nchash", "nc_sync_user_id", "Hash Values"
    )

    user_has_calendar = fields.Boolean(
        "User has calendar", compute="compute_user_has_calendar"
    )
    sync_calendar = fields.Boolean("Sync Calendar")
    nc_email = fields.Char("Email")

    @api.depends("user_id", "user_name", "nc_password")
    def compute_user_has_calendar(self):
        """
        This method determine if current Odoo user
        have Nextcloud calendar records
        """
        for user in self:
            user.user_has_calendar = (
                True if user.user_id and user.user_id.nc_calendar_ids else False
            )

    @api.constrains("user_id", "user_name")
    def check_user_exist(self):
        for user in self:
            sync_user_id = self.search(
                [
                    "&",
                    ("id", "!=", user.id),
                    "|",
                    ("user_id", "=", user.user_id.id),
                    ("user_name", "=ilike", user.user_name),
                ],
                limit=1,
            )
            if sync_user_id:
                raise ValidationError(
                    _(
                        "Existing configuration found. The selected Odoo User '%s'"
                        " or Nextcloud Username '%s' is already mapped to an"
                        " existing record" % (user.user_id.name, user.user_name)
                    )
                )

    @api.onchange("nc_calendar_id")
    def onchange_nc_calendar_id(self):
        if self.nc_calendar_id:
            self.sync_calendar = True
            self.user_message = (
                "%s will be used as your default Odoo "
                "calendar when creating new events"
            ) % self.nc_calendar_id.name
        else:
            self.sync_calendar = False
            self.user_message = (
                "'Default Calendar' field will be used as "
                "your default odoo calendar when creating "
                "new events"
            )

    def write(self, vals):
        if "nc_calendar_id" in vals:
            calendar_event_ids = (
                self.env["calendar.event"]
                .search(
                    [
                        "|",
                        ("user_id", "=", self.user_id.id),
                        ("partner_ids", "in", self.user_id.partner_id.id),
                        ("nc_synced", "=", False),
                    ]
                )
                .filtered(
                    lambda x: not x.nc_calendar_ids
                    or (
                        x.nc_calendar_ids
                        and self.user_id.partner_id.id not in x.nc_calendar_ids.ids
                    )
                )
            )
            calendar_ids = calendar_event_ids.nc_calendar_ids.filtered(
                lambda x: x.user_id == self.user_id
            )
            for calendar_id in calendar_ids:
                calendar_event_ids.nc_calendar_ids = [(3, calendar_id.id)]
            calendar_event_ids.with_context(sync=True).write(
                {"nc_calendar_ids": [(4, vals["nc_calendar_id"])]}
            )
        return super(NcSyncUser, self).write(vals)

    def unlink(self):
        sync_user_ids = self.search([]).mapped("user_id")
        for record in self:
            if record.user_id:
                calendar_ids = record.env["calendar.event"].search(
                    [("user_id", "=", record.user_id.id)]
                )
                for calendar in calendar_ids:
                    # Check if the calendar event is shared with another
                    # Odoo user with an active Nextcloud calendar sync
                    if len(calendar.nc_hash_ids.ids) > 1:
                        hash_ids = calendar.nc_hash_ids.filtered(
                            lambda x: x.user_id == record.user_id
                            or (sync_user_ids and x.user_id not in sync_user_ids)
                        )
                        hash_ids.unlink()
                        if not calendar.nc_hash_ids:
                            calendar.write({"nc_uid": False, "nc_synced": False})
                    # Otherwise, remove both the nc_uid and hash_ids
                    else:
                        calendar.write({"nc_uid": False, "nc_synced": False})
                        calendar.nc_hash_ids.unlink()
                # Remove all Nextcloud calendar records
                nc_calendar_ids = record.user_id.nc_calendar_ids
                nc_calendar_ids.unlink()
        return super(NcSyncUser, self).unlink()

    def save_user_config(self):
        # Close the pop-up and display the calendar event records
        return self.env.ref("calendar.action_calendar_event").sudo().read()[0]

    def get_user_connection(self):
        """
        This method returns the connection and principal
        object from Nextcloud server
        """
        params = {
            "nextcloud_login": "Login",
            "nextcloud_password": "Password",
            "nextcloud_url": "Server URL",
        }
        for item in params:
            value = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("nextcloud_odoo_sync.%s" % item)
            )
            if not value:
                raise ValidationError(
                    _(
                        "Missing value for '%s' field in Settings/ Nextcloud"
                        % params[item]
                    )
                )

        nc_url = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("nextcloud_odoo_sync.nextcloud_url")
            + "/remote.php/dav"
        )
        connection, principal = self.env["nextcloud.caldav"].check_nextcloud_connection(
            url=nc_url, username=self.user_name, password=self.nc_password
        )
        if isinstance(principal, dict):
            sync_error = principal["sync_error_id"].name
            response = principal["response_description"]
            raise ValidationError(f"{sync_error}: {response}")
        user_data = self.env["nextcloud.base"].get_user(principal.client.username)
        self.nc_email = user_data.get("email", False) if user_data else False
        return {"connection": connection, "principal": principal}

    def get_user_calendars(self, principal):
        """
        This method gets all the calendar records of the
        Nextcloud user and create it in Odoo if not exist
        :param connection: CalDav connection principal object
        """
        nc_calendars = principal.calendars()
        nc_calendars = [
            cal for cal in nc_calendars if "shared_by" not in cal.canonical_url
        ]
        # Remove Nextcloud calendars in Odoo if the canonical URL
        # no longer exist in Nextcloud
        calendar_not_in_odoo_ids = self.nc_calendar_id.search(
            [
                (
                    "calendar_url",
                    "not in",
                    [str(x.canonical_url) for x in nc_calendars],
                ),
                ("user_id", "=", self.user_id.id),
            ]
        )
        if calendar_not_in_odoo_ids:
            calendar_not_in_odoo_ids.sudo().unlink()
        result = []
        nc_calendar_ids = self.nc_calendar_id.search(
            [("user_id", "=", self.user_id.id)]
        )
        for record in nc_calendars:
            nc_calendar_id = nc_calendar_ids.filtered(
                lambda x: x.name == record.name
                and x.calendar_url == record.canonical_url
            )
            if not nc_calendar_id:
                result.append(
                    {
                        "name": record.name,
                        "user_id": self.user_id.id,
                        "calendar_url": record.canonical_url,
                    }
                )
        if result:
            self.env["nc.calendar"].sudo().create(result)

    def check_nc_connection(self):
        """
        This method connects to Nextcloud server using the
        username and password provided for the user and
        triggers the creation of Nextcloud Calendar record
        for use in creating events in Odoo
        """
        connection_dict = self.sudo().get_user_connection()
        principal = connection_dict.get("principal", False)
        self.get_user_calendars(principal)
        target = "new" if self._context.get("pop_up") else "main"
        res = {
            "name": "NextCloud User Setup",
            "view_mode": "form",
            "res_model": "nc.sync.user",
            "type": "ir.actions.act_window",
            "target": target,
            "res_id": self.id,
        }
        if target == "main":
            res.update(
                {"context": {"form_view_initial_mode": "edit", "no_footer": True}}
            )
        return res

    def get_event_data(self, event):
        """
        This method returns the following data of an event:
        UID, hash, dictionary of event values
        :param event: Calendar event object
        :return dictionary of event values
        """
        event_vals = jicson.fromText(event.data).get("VCALENDAR")[0].get("VEVENT")
        data = []
        # Remove the DTSTAMP values as it always changes
        # when event get queried from Nextcloud
        for d in event_vals:
            nc_uid = d["UID"]
            d.pop("DTSTAMP")
            d.pop("SEQUENCE", False)
            exdate_key = [k for k, v in d.items() if "EXDATE" in k]
            vevent = event.vobject_instance.vevent
            if isinstance(vevent.dtstart.value, datetime):
                date_format = "%Y%m%dT%H%M%S"
            else:
                date_format = "%Y%m%d"
            if exdate_key:
                tz = False
                if "TZID" in exdate_key[0]:
                    tz = exdate_key[0].split("=")[1]
                d.pop(exdate_key[0])
                d["exdates"] = [
                    x.value[0].strftime(date_format) for x in vevent.exdate_list
                ]
                d["exdate_tz"] = tz
            data.append(d)
        vals = {"data": data}
        vals["uid"] = nc_uid
        json_data = str(json.dumps(vals["data"], sort_keys=True)).encode("utf-8")
        vals["hash"] = hashlib.sha1(json_data).hexdigest()
        return vals

    def get_all_user_events(self, **params):
        """
        This method get all user events in both Odoo and Nextcloud
        :param log_id: single recordset of nc.sync.log model
        :params **params: dictionary of multiple recordsets
                from different models
        """
        events = params["all_odoo_event_ids"]
        log_obj = params["log_obj"]
        result = {
            "od_events": [],
            "nc_events": [],
            "connection": False,
            "principal": False,
        }
        for user in self:
            if not events:
                events = self.env["calendar.event"].sudo().search([])
            try:
                connection_dict = self.get_user_connection()
                principal = connection_dict.get("principal", False)
                result["principal"] = principal
                result["connection"] = connection_dict.get("connection", False)
            except Exception as error:
                if log_obj:
                    log_obj.log_event("error", error=error, message="Nextcloud:")
                    return result
                else:
                    raise ValidationError(_(error))
            try:
                # Get all Odoo events where user is organizer or attendee
                od_event_ids = events.filtered(
                    lambda x: x.user_id == user.user_id
                    or (x.partner_ids and user.partner_id in x.partner_ids)
                )
                for event in od_event_ids:
                    if (
                        event.user_id in params["all_sync_user_ids"].mapped("user_id")
                        and not event.nc_uid
                        and event.user_id != user.user_id
                    ):
                        continue
                    event_hash = False
                    if event.nc_hash_ids:
                        event_hash = event.nc_hash_ids.filtered(
                            lambda x: x.nc_sync_user_id == user
                        ).mapped("nc_event_hash")
                    result["od_events"].append(
                        {
                            "nc_uid": event.nc_uid,
                            "od_event": event,
                            "event_hash": event_hash[0] if event_hash else False,
                        }
                    )
                # Get all Nextcloud events of the user
                for calendar in principal.calendars():
                    # Check if calendar exist for the user and make sure
                    # it has the same name as the Nextcloud calendar in case
                    # the user rename it in Nextcloud, otherwise create a new
                    # calendar if not exist
                    if "shared_by" in calendar.canonical_url:
                        continue
                    nc_calendar_id = self.nc_calendar_id.search(
                        [
                            ("user_id", "=", self.user_id.id),
                            ("calendar_url", "=", calendar.canonical_url),
                        ],
                        limit=1,
                    )
                    if nc_calendar_id:
                        if calendar.name != nc_calendar_id.name:
                            nc_calendar_id.name = calendar.name
                    else:
                        self.env["nc.calendar"].sudo().create(
                            {
                                "name": calendar.name,
                                "user_id": user.user_id.id,
                                "calendar_url": calendar.canonical_url,
                            }
                        )
                    for item in calendar.events():
                        if "organizer" in item.instance.vevent.contents:
                            organizer_email = (
                                item.instance.vevent.organizer.value.replace(
                                    "mailto:", ""
                                )
                            )
                            if organizer_email != user.nc_email:
                                continue
                        event_vals = user.get_event_data(item)
                        result["nc_events"].append(
                            {
                                "nc_uid": event_vals["uid"],
                                "event_hash": event_vals["hash"],
                                "nc_event": event_vals["data"],
                                "nc_caldav": item,
                            }
                        )
                return result
            except Exception as error:
                if log_obj:
                    log_obj.log_event("error", error=error, message="Nextcloud:")
                    return result
                else:
                    raise ValidationError(_(error))

    def get_nc_event_hash_by_uid(self, nc_uid):
        for user in self:
            connection_dict = user.get_user_connection()
            principal = connection_dict["principal"]
            for calendar in principal.calendars():
                # Check if calendar exist for the user and make sure
                # it has the same name as the Nextcloud calendar in case
                # the user rename it in Nextcloud, otherwise create a new
                # calendar if not exist
                if "shared_by" in calendar.canonical_url:
                    continue
                nc_calendar_id = self.nc_calendar_id.search(
                    [
                        ("user_id", "=", self.user_id.id),
                        ("calendar_url", "=", calendar.canonical_url),
                    ],
                    limit=1,
                )
                if nc_calendar_id:
                    if calendar.name != nc_calendar_id.name:
                        nc_calendar_id.name = calendar.name
                else:
                    self.env["nc.calendar"].sudo().create(
                        {
                            "name": calendar.name,
                            "user_id": user.user_id.id,
                            "calendar_url": calendar.canonical_url,
                        }
                    )
                for item in calendar.events():
                    event_vals = user.get_event_data(item)
                    if event_vals["uid"] == nc_uid:
                        return event_vals["hash"]
        return False
