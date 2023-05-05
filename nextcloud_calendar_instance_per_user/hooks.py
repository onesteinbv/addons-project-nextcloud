# Copyright 2023 Anjeel Haria
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import SUPERUSER_ID, api


def populate_nextcloud_url(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    default_nextcloud_url = env["ir.config_parameter"].sudo().get_param("nextcloud_odoo_sync.nextcloud_url")
    if default_nextcloud_url:
        env["nc.sync.user"].search([]).write({'nextcloud_url': default_nextcloud_url})


def uninstall_hook(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    default_nextcloud_url = env["ir.config_parameter"].sudo().get_param("nextcloud_odoo_sync.nextcloud_url")
    if default_nextcloud_url:
        env["nc.sync.user"].search([('nextcloud_url', 'not ilike', default_nextcloud_url)]).unlink()
