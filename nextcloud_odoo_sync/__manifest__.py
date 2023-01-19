# -*- coding: utf-8 -*-
# Copyright (c) 2023 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)
{
    'name': 'Nextcloud-Odoo Sync',
    'version': '15.0.0.0.1',
    'category': 'Others',
    'description': """Sync Nextcloud apps into Odoo""",
    'author': 'iScale Solutions Inc.',
    'website': 'http://iscale-solutions.com',
    'external_dependencies': {'python': ['caldav']},
    'depends': ['base', 'calendar', 'resource'],
    "maintainers": ["iscale-solutions"],
    "license": "AGPL-3",
    'data': ['data/res_groups_data.xml',
             'data/nc_sync_error_data.xml',
             'security/ir.model.access.csv',
             'views/calendar_event_views.xml',
             'views/nc_sync_user_views.xml',
             'views/nc_sync_log_views.xml',
             'views/nc_sync_error_views.xml',
             'views/res_config_settings_views.xml'],
    'installable': True,
    'active': False,
    'auto_install': False,
    'application': True,
}
