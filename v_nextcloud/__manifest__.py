# Copyright (c) 2020 iScale Solutions Inc.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)
{
    "name": "NextCloud-Odoo Sync Test Module",
    "version": "15.0.0.2",
    "category": "Others",
    "description": """NextCloud sync test module""",
    "author": "iScale Solutions Inc.",
    "website": "http://iscale-solutions.com",
    "external_dependencies": {"python": ["caldav"]},
    "depends": ["base", "calendar", "nextcloud_odoo_sync"],
    "maintainers": ["iscale-solutions"],
    "license": "AGPL-3",
    "data": ["security/ir.model.access.csv", "views/nextcloud_views.xml"],
    "installable": True,
    "active": False,
    "auto_install": False,
    "application": True,
}
