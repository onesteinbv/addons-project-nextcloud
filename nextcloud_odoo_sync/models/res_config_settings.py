from odoo import fields, models, api

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'
    
    enable_calendar_sync = fields.Boolean("Enable Calendar Syc")
    nextcloud_url = fields.Char(string="Server URL")
    nextcloud_login = fields.Char(string="Login")
    nextcloud_password = fields.Char(string="Password")
    nextcloud_connection_status = fields.Selection([('online', 'Online'), ('fail', 'Failed to login')], "Connection Status")
    nextcloud_error = fields.Text(string="Error")
    
    @api.model
    def set_values(self):
        res = super(ResConfigSettings, self).set_values()
        self.env['ir.config_parameter'].sudo().set_param('nextcloud_odoo_sync.enable_calendar_sync', self.enable_calendar_sync)
        self.env['ir.config_parameter'].sudo().set_param('nextcloud_odoo_sync.nextcloud_url', self.nextcloud_url)
        self.env['ir.config_parameter'].sudo().set_param('nextcloud_odoo_sync.nextcloud_login', self.nextcloud_login)
        self.env['ir.config_parameter'].sudo().set_param('nextcloud_odoo_sync.nextcloud_password', self.nextcloud_password)
        self.env['ir.config_parameter'].sudo().set_param('nextcloud_odoo_sync.nextcloud_connection_status', self.nextcloud_connection_status)
        self.env['ir.config_parameter'].sudo().set_param('nextcloud_odoo_sync.nextcloud_error', self.nextcloud_error)
        return res

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        res.update(
            enable_calendar_sync=self.env['ir.config_parameter'].sudo().get_param('nextcloud_odoo_sync.enable_calendar_sync'),
            nextcloud_url=self.env['ir.config_parameter'].sudo().get_param('nextcloud_odoo_sync.nextcloud_url'),
            nextcloud_login=self.env['ir.config_parameter'].sudo().get_param('nextcloud_odoo_sync.nextcloud_login'),
            nextcloud_password=self.env['ir.config_parameter'].sudo().get_param('nextcloud_odoo_sync.nextcloud_password'),
            nextcloud_connection_status=self.env['ir.config_parameter'].sudo().get_param('nextcloud_odoo_sync.nextcloud_connection_status'),
            nextcloud_error=self.env['ir.config_parameter'].sudo().get_param('nextcloud_odoo_sync.nextcloud_error'),
        )
        return res