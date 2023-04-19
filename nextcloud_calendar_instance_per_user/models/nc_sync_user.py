# Copyright 2023 Anjeel Haria
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class NcSyncUser(models.Model):
    _inherit = "nc.sync.user"

    nextcloud_url = fields.Char(string="Server URL", required=True)

    @api.model
    def default_get(self, fields):
        """
        Inherited to add default server url for each of the users
        """
        res = super(NcSyncUser, self).default_get(fields)
        res["nextcloud_url"] = self.env["ir.config_parameter"].sudo().get_param("nextcloud_odoo_sync.nextcloud_url")
        return res

    @api.model
    def create(self, vals):
        if vals.get('nextcloud_url',False):
            vals["nextcloud_url"] = vals["nextcloud_url"].strip("/")
        return super(NcSyncUser, self).create(vals)

    def write(self, vals):
        if vals.get('nextcloud_url',False):
            vals["nextcloud_url"] = vals["nextcloud_url"].strip("/")
        return super(NcSyncUser, self).write(vals)

    def get_user_connection(self):
        """
        Overriden to use nextcloud server url on a per user basis
        """
        params = {
            "nextcloud_login": "Login",
            "nextcloud_password": "Password",
            "nextcloud_url": "Server URL",
        }
        config_param_obj = self.env["ir.config_parameter"].sudo()
        for item in params:
            value = config_param_obj.get_param("nextcloud_odoo_sync.%s" % item)
            if not value:
                raise ValidationError(
                    _(
                        "Missing value for '%s' field in Settings/ Nextcloud"
                        % params[item]
                    )
                )

        nc_url = (
            (self.nextcloud_url or config_param_obj.get_param("nextcloud_odoo_sync.nextcloud_url"))
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


