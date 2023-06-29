# Copyright 2023 Anjeel Haria
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "NextCloud Calendar Instance Per User",
    "summary": "Allows users to specify their own nextcloud instance",
    "version": "16.0.1.0.0",
    "category": "Others",
    "author": "Onestein",
    "website": "https://github.com/OCA/vertical-association",
    "license": "AGPL-3",
    "application": True,
    "installable": True,
    "depends": ["nextcloud_odoo_sync"],
    "data": [
        "views/nc_sync_user_views.xml",
    ],
    "post_init_hook": "populate_nextcloud_url",
    "uninstall_hook": "uninstall_hook",
}
