# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import fields, models, api


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    enable_calendar_sync = fields.Boolean("Enable Calendar Syc")
    nextcloud_url = fields.Char(string="Server URL")
    nextcloud_login = fields.Char(string="Login")
    nextcloud_password = fields.Char(string="Password")
    nextcloud_connection_status = fields.Selection(
        [("online", "Online"), ("fail", "Failed to login")], "Connection Status"
    )
    nextcloud_error = fields.Text("Error")

    @api.model
    def set_values(self):
        res = super(ResConfigSettings, self).set_values()
        self.env["ir.config_parameter"].sudo().set_param(
            "nextcloud_odoo_sync.enable_calendar_sync", self.enable_calendar_sync
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "nextcloud_odoo_sync.nextcloud_url", self.nextcloud_url.strip("/")
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "nextcloud_odoo_sync.nextcloud_login", self.nextcloud_login
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "nextcloud_odoo_sync.nextcloud_password", self.nextcloud_password
        )

        if (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("nextcloud_odoo_sync.enable_calendar_sync")
        ):
            connection, principal = self.env[
                "nextcloud.caldav"
            ].check_nextcloud_connection(
                url=self.nextcloud_url + "/remote.php/dav",
                username=self.nextcloud_login,
                password=self.nextcloud_password,
            )
            if isinstance(principal, dict) and principal.get("sync_error_id"):
                self.env["ir.config_parameter"].sudo().set_param(
                    "nextcloud_odoo_sync.nextcloud_connection_status", "fail"
                )
                self.env["ir.config_parameter"].sudo().set_param(
                    "nextcloud_odoo_sync.nextcloud_error",
                    principal.get("response_description"),
                )
            else:
                self.env["ir.config_parameter"].sudo().set_param(
                    "nextcloud_odoo_sync.nextcloud_connection_status", "online"
                )
                self.env["ir.config_parameter"].sudo().set_param(
                    "nextcloud_odoo_sync.nextcloud_error", False
                )
        return res

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        res.update(
            enable_calendar_sync=self.env["ir.config_parameter"]
            .sudo()
            .get_param("nextcloud_odoo_sync.enable_calendar_sync"),
            nextcloud_url=self.env["ir.config_parameter"]
            .sudo()
            .get_param("nextcloud_odoo_sync.nextcloud_url"),
            nextcloud_login=self.env["ir.config_parameter"]
            .sudo()
            .get_param("nextcloud_odoo_sync.nextcloud_login"),
            nextcloud_password=self.env["ir.config_parameter"]
            .sudo()
            .get_param("nextcloud_odoo_sync.nextcloud_password"),
            nextcloud_connection_status=self.env["ir.config_parameter"]
            .sudo()
            .get_param("nextcloud_odoo_sync.nextcloud_connection_status"),
            nextcloud_error=self.env["ir.config_parameter"]
            .sudo()
            .get_param("nextcloud_odoo_sync.nextcloud_error"),
        )
        return res
