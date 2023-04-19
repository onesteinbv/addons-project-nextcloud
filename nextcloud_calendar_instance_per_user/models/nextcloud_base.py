# Copyright 2023 Anjeel Haria
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import requests
from odoo import models
from odoo.http import request


class NextcloudBase(models.AbstractModel):
    _inherit = "nextcloud.base"

    def get_auth_data(self):
        """
        Overriden to use nextcloud user credentials instead of default credentials
        """
        data = super(NextcloudBase, self).get_auth_data()
        if self._context.get('nextcloud_login',False):
            data["auth_pk"] = (
                self._context.get('nextcloud_login'),
                self._context.get('nextcloud_password'),
            )
        return data

    def get_full_url(self, additional_url="", api_url=""):
        """
        Overriden to use nextcloud user server url instead of default server url
        """
        if additional_url and not str(additional_url).startswith("/"):
            additional_url = "/{}".format(additional_url)
        config_obj = self.env["ir.config_parameter"]
        base_url = self._context.get('base_url', False) or config_obj.sudo().get_param(
            "nextcloud_odoo_sync.nextcloud_url")
        res = "{base_url}{api_url}{additional_url}".format(
            base_url=base_url,
            api_url=api_url,
            additional_url=additional_url,
        )
        res += "?format=json"
        return res

    def get_users(self, search=None, limit=None, offset=None):
        """
        Retrieve a list of users from the Nextcloud server
        :param search: string, optional search string
        :param limit: int, optional limit value
        :param offset: int, optional offset value
        :return: List of users info
        """
        result = super(NextcloudBase, self).get_users(search=search, limit=limit, offset=offset)
        odoo_users = self.env["nc.sync.user"].search([("sync_calendar", "=", True)])
        server_url = self.env["ir.config_parameter"].sudo().get_param("nextcloud_odoo_sync.nextcloud_url")
        for user in odoo_users:
            if user.nextcloud_url != server_url:
                user_result = self.with_context(base_url=user.nextcloud_url, nextcloud_login=user.user_name,
                                           nextcloud_password=user.nc_password).get(params=params)
                if isinstance(user_result, dict):
                    users = []
                    for uid in user_result["ocs"]["data"]["users"]:
                        res = self.with_context(base_url=user.nextcloud_url, nextcloud_login=user.user_name,
                                           nextcloud_password=user.nc_password).get(uid)
                        users.append(res["ocs"]["data"])
                    result["ocs"]["data"]["users"].extend(users)
        return result
