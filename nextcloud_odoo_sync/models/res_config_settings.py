# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import fields, models, api


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    enable_calendar_sync = fields.Boolean(
        "Enable Calendar Sync",
        config_parameter="nextcloud_odoo_sync.enable_calendar_sync",
    )
    nextcloud_url = fields.Char(string="Server URL")
    nextcloud_login = fields.Char(
        string="Login", config_parameter="nextcloud_odoo_sync.nextcloud_login"
    )
    nextcloud_password = fields.Char(
        string="Password", config_parameter="nextcloud_odoo_sync.nextcloud_password"
    )
    nextcloud_connection_status = fields.Selection(
        [("online", "Online"), ("fail", "Failed to login")], "Connection Status"
    )
    nextcloud_error = fields.Text("Error")
    log_capacity = fields.Integer(
        string="Log Capacity",
        default=7,
        config_parameter="nextcloud_odoo_sync.log_capacity",
    )
    monthly_recurring_events_limit = fields.Integer(
        string="Monthly Recurring Events Limit",
        default=2,
        config_parameter="nextcloud_odoo_sync.monthly_recurring_events_limit",
    )
    daily_recurring_events_limit = fields.Integer(
        string="Daily Recurring Events Limit",
        default=2,
        config_parameter="nextcloud_odoo_sync.daily_recurring_events_limit",
    )
    weekly_recurring_events_limit = fields.Integer(
        string="Weekly Recurring Events Limit",
        default=2,
        config_parameter="nextcloud_odoo_sync.weekly_recurring_events_limit",
    )
    yearly_recurring_events_limit = fields.Integer(
        string="Yearly Recurring Events Limit",
        default=10,
        config_parameter="nextcloud_odoo_sync.yearly_recurring_events_limit",
    )




    @api.model
    def set_values(self):
        res = super(ResConfigSettings, self).set_values()
        ir_config_paramater_obj = self.env["ir.config_parameter"].sudo()
        if ir_config_paramater_obj.get_param(
            "nextcloud_odoo_sync.enable_calendar_sync"
        ):
            nextcloud_url = self.nextcloud_url.strip("/")
            connection, principal = self.env[
                "nextcloud.caldav"
            ].check_nextcloud_connection(
                url=nextcloud_url + "/remote.php/dav",
                username=self.nextcloud_login,
                password=self.nextcloud_password,
            )
            if isinstance(principal, dict) and principal.get("sync_error_id"):
                nextcloud_connection_status = "fail"
                nextcloud_error = principal.get("response_description")
            else:
                nextcloud_connection_status = "online"
                nextcloud_error = False
            ir_config_paramater_obj.set_param(
                "nextcloud_odoo_sync.nextcloud_url", nextcloud_url
            )
            ir_config_paramater_obj.set_param(
                "nextcloud_odoo_sync.nextcloud_connection_status",
                nextcloud_connection_status,
            )
            ir_config_paramater_obj.set_param(
                "nextcloud_odoo_sync.nextcloud_error", nextcloud_error
            )
        return res

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ir_config_paramater_obj = self.env["ir.config_parameter"].sudo()
        res.update(
            nextcloud_url=ir_config_paramater_obj.get_param(
                "nextcloud_odoo_sync.nextcloud_url"
            ),
            nextcloud_connection_status=ir_config_paramater_obj.get_param(
                "nextcloud_odoo_sync.nextcloud_connection_status"
            ),
            nextcloud_error=ir_config_paramater_obj.get_param(
                "nextcloud_odoo_sync.nextcloud_error"
            ),
        )
        return res
