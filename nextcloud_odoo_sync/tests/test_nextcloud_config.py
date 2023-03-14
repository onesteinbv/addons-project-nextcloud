# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

from odoo.tests import common


class TestNextCloudConfig(common.TransactionCase):
    def test_nextcloud_config(self):
        config_obj = self.env["ir.config_parameter"]
        self.assertEqual(
            config_obj.sudo().get_param(
                "nextcloud_odoo_sync.nextcloud_connection_status"
            ),
            False,
        )
        config_obj.sudo().set_param(
            "nextcloud_odoo_sync.nextcloud_connection_status", "online"
        )
        self.assertEqual(
            config_obj.sudo().get_param(
                "nextcloud_odoo_sync.nextcloud_connection_status"
            ),
            "online",
        )
        config_obj.sudo().set_param(
            "nextcloud_odoo_sync.nextcloud_connection_status", False
        )
